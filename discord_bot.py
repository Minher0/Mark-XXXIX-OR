"""
discord_bot.py — Jarvis as a Discord Bot

Run: python discord_bot.py
Or: python main.py --discord

Features:
- Responds to mentions (@Jarvis) and DMs
- Uses Gemini API for smart responses
- Supports text commands: !jarvis <question>
- Can use all Jarvis tools (web search, weather, etc.)
- Remembers conversation context per channel
"""

import asyncio
import json
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# ═══════════════════════════════════════════════════════════
# CONFIG
# ═══════════════════════════════════════════════════════════

API_KEYS_PATH = BASE_DIR / "config" / "api_keys.json"
SYSTEM_PROMPT_PATH = BASE_DIR / "core" / "prompt.txt"
MEMORY_PATH = BASE_DIR / "memory" / "long_term.json"

MAX_HISTORY = 30          # Max messages per channel context
MAX_RESPONSE = 2000       # Discord message limit
RESPONSE_COOLDOWN = 2     # Seconds between responses per channel
READ_HISTORY_DEFAULT = 15 # Default number of messages to read from channel history
READ_HISTORY_MAX = 50     # Max number of messages to read from channel history


def _load_keys() -> dict:
    try:
        with open(API_KEYS_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _load_system_prompt() -> str:
    base = (
        "You are JARVIS, a sharp, efficient AI assistant running on Discord. "
        "Be concise (Discord messages should be short). "
        "Use markdown formatting when helpful. "
        "You have access to tools for web search, weather, reminders, and more."
    )
    if SYSTEM_PROMPT_PATH.exists():
        base = SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")
    # Add memory context
    if MEMORY_PATH.exists():
        try:
            mem = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
            if mem:
                base += f"\n\nUser memory:\n{json.dumps(mem, indent=2, ensure_ascii=False)}"
        except Exception:
            pass
    return base


# ═══════════════════════════════════════════════════════════
# GEMINI CHAT (for Discord text responses)
# ═══════════════════════════════════════════════════════════

class GeminiChat:
    """Lightweight Gemini chat for Discord — no audio, just text + tools."""

    # Discord-specific tool declarations (uses user's personal token, not bot token)
    DISCORD_BOT_TOOLS = [
        {
            "name": "read_channel_history",
            "description": (
                "Reads recent messages from the current Discord channel to get conversation context, "
                "using the user's personal Discord account (not the bot account). "
                "Use this when you need to understand what people are talking about before responding, "
                "for example when asked to 'respond to someone' or 'answer this person'. "
                "Returns the last N messages with author names and timestamps. "
                "You don't need to use this every time — only when you need context about the ongoing conversation."
            ),
            "parameters": {
                "type": "OBJECT",
                "properties": {
                    "limit": {
                        "type": "INTEGER",
                        "description": f"Number of recent messages to read (default: {READ_HISTORY_DEFAULT}, max: {READ_HISTORY_MAX})"
                    }
                },
                "required": []
            }
        }
    ]

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.model = "gemini-2.5-flash-preview-05-20"
        self.system_prompt = _load_system_prompt()
        # Per-channel conversation history
        self._histories: dict[str, list] = {}
        # Current Discord channel info (set per-request)
        self._current_channel_id: str | None = None
        self._current_channel_name: str | None = None
        self._current_guild_name: str | None = None
        self._bot_user_id: str | None = None
        # Load tool declarations
        self.tools = []
        self._load_tools()

    def _load_tools(self):
        try:
            from main import TOOL_DECLARATIONS
            # Merge main tools + Discord-specific tools
            all_declarations = TOOL_DECLARATIONS + self.DISCORD_BOT_TOOLS
            self.tools = [{"function_declarations": all_declarations}]
        except ImportError:
            print("[DiscordBot] ⚠️ Could not load tool declarations")
            # Still add Discord-specific tools
            self.tools = [{"function_declarations": self.DISCORD_BOT_TOOLS}]

    def _get_history(self, channel_id: str) -> list:
        if channel_id not in self._histories:
            self._histories[channel_id] = []
        return self._histories[channel_id]

    def _trim_history(self, channel_id: str):
        hist = self._histories.get(channel_id, [])
        if len(hist) > MAX_HISTORY:
            self._histories[channel_id] = hist[-MAX_HISTORY:]

    async def read_channel_messages(self, limit: int = READ_HISTORY_DEFAULT) -> str:
        """Read recent messages from the current Discord channel using user token (raw HTTP API)."""
        if not self._current_channel_id:
            return "No Discord channel available."

        try:
            from actions.discord_control import _api_get, _get_token
            # Verify the user token is configured
            try:
                _get_token()
            except (ValueError, FileNotFoundError) as e:
                return f"Cannot read channel history: {e}"

            limit = min(max(1, limit), READ_HISTORY_MAX)

            # Fetch messages via Discord HTTP API (user token)
            raw_messages = await asyncio.to_thread(
                _api_get,
                f"/channels/{self._current_channel_id}/messages",
                {"limit": limit}
            )

            if not raw_messages:
                return "No messages found or could not read channel."

            # Format messages chronologically (oldest first — API returns newest first)
            lines = []
            for msg in reversed(raw_messages):
                author = msg.get("author", {}).get("username", "Unknown")
                global_name = msg.get("author", {}).get("global_name")
                if global_name:
                    author = f"{global_name} ({author})"
                # Skip bot's own messages
                author_id = msg.get("author", {}).get("id", "")
                if self._bot_user_id and author_id == self._bot_user_id:
                    continue
                content = msg.get("content", "") or "(embed/attachment)"
                # Add timestamp
                timestamp_str = msg.get("timestamp", "")
                try:
                    dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                    time_fmt = dt.strftime("%H:%M")
                except Exception:
                    time_fmt = "?"
                lines.append(f"[{time_fmt}] {author}: {content}")

            if not lines:
                return "No messages found in this channel."

            channel_name = self._current_channel_name or "DM"
            server_info = f" ({self._current_guild_name})" if self._current_guild_name else ""
            header = f"Last {len(lines)} messages in #{channel_name}{server_info}:\n"
            return header + "\n".join(lines)

        except ValueError as e:
            return f"Discord auth error: {e}"
        except Exception as e:
            return f"Error reading channel history: {e}"

    async def chat(self, text: str, channel_id: str, discord_channel=None, bot_user=None) -> str:
        """Send a message and get a response using Gemini API.
        
        Args:
            text: The user's message text
            channel_id: Channel ID string for history tracking
            discord_channel: The discord.Channel object (to get channel name/guild info)
            bot_user: The bot's discord.User object (to get bot user ID for filtering)
        """
        # Store channel info for read_channel_history tool (uses user token, not bot)
        self._current_channel_id = channel_id
        self._current_channel_name = getattr(discord_channel, 'name', None) if discord_channel else None
        guild = getattr(discord_channel, 'guild', None) if discord_channel else None
        self._current_guild_name = guild.name if guild else None
        self._bot_user_id = str(bot_user.id) if bot_user else None
        try:
            import google.genai as genai
            from google.genai import types

            client = genai.Client(api_key=self.api_key)

            history = self._get_history(channel_id)
            history.append({"role": "user", "content": text})

            # Build contents for Gemini
            contents = []
            for msg in history:
                contents.append(
                    types.Content(
                        role=msg["role"],
                        parts=[types.Part.from_text(text=msg["content"])]
                    )
                )

            # Call Gemini with tools
            config = types.GenerateContentConfig(
                system_instruction=self.system_prompt,
                tools=self.tools if self.tools else None,
                temperature=0.8,
                max_output_tokens=2048,
            )

            response = await asyncio.to_thread(
                client.models.generate_content,
                model=self.model,
                contents=contents,
                config=config,
            )

            # Check for tool calls — may need multiple rounds
            if response.candidates and response.candidates[0].content.parts:
                # Collect all function calls and text parts
                function_calls = []
                result_text = ""
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)
                    elif part.text:
                        result_text += part.text

                # Execute function calls and feed results back to Gemini
                if function_calls:
                    # Add the model's response (with function calls) to history
                    history.append({"role": "model", "content": response.candidates[0].content.parts})

                    for fc in function_calls:
                        # Execute the tool
                        tool_result = await self._execute_tool(
                            fc.name,
                            dict(fc.args) if fc.args else {}
                        )

                        # For read_channel_history, feed the result back to Gemini
                        # so it can use the context to generate a proper response
                        if fc.name == "read_channel_history":
                            # Add tool result as a function response, then continue conversation
                            history.append({"role": "user", "content": f"[Channel History Result]\n{tool_result}"})

                            # Re-call Gemini with the channel context now available
                            contents = []
                            for msg in history:
                                if isinstance(msg.get("content"), list):
                                    # Model response with function calls — reconstruct
                                    parts = []
                                    for p in msg["content"]:
                                        if hasattr(p, 'text') and p.text:
                                            parts.append(types.Part.from_text(text=p.text))
                                        elif hasattr(p, 'function_call') and p.function_call:
                                            parts.append(types.Part.from_function_call(
                                                name=p.function_call.name,
                                                args=dict(p.function_call.args) if p.function_call.args else {}
                                            ))
                                    contents.append(types.Content(role=msg["role"], parts=parts))
                                else:
                                    contents.append(types.Content(
                                        role=msg["role"],
                                        parts=[types.Part.from_text(text=msg["content"])]
                                    ))

                            followup_response = await asyncio.to_thread(
                                client.models.generate_content,
                                model=self.model,
                                contents=contents,
                                config=config,
                            )

                            if followup_response.text:
                                history.append({"role": "model", "content": followup_response.text})
                                self._trim_history(channel_id)
                                return followup_response.text
                            return "Je n'ai pas pu générer de réponse après avoir lu l'historique."
                        else:
                            result_text += f"🔧 **{fc.name}**: {tool_result[:500]}\n"

                if result_text.strip():
                    history.append({"role": "model", "content": result_text})
                    self._trim_history(channel_id)
                    return result_text.strip()

            # Simple text response
            if response.text:
                history.append({"role": "model", "content": response.text})
                self._trim_history(channel_id)
                return response.text

            return "Je n'ai pas pu générer de réponse."

        except Exception as e:
            print(f"[DiscordBot] ❌ Gemini error: {e}")
            traceback.print_exc()
            return f"Erreur Gemini: {str(e)[:200]}"

    async def _execute_tool(self, name: str, args: dict) -> str:
        """Execute a tool and return the result."""
        try:
            # Discord-specific tool: read_channel_history (uses user token via HTTP API)
            if name == "read_channel_history":
                limit = int(args.get("limit", READ_HISTORY_DEFAULT))
                result = await self.read_channel_messages(limit)
                return str(result)

            # Import tool dispatch from local_mode or main
            # We'll use a simplified version here
            from actions.web_search import web_search as web_search_action
            from actions.weather_report import weather_action
            from actions.reminder import reminder
            from actions.cmd_control import cmd_control
            from actions.discord_control import discord_control

            tool_map = {
                "web_search": lambda: web_search_action(parameters=args, player=None),
                "weather_report": lambda: weather_action(parameters=args, player=None),
                "set_reminder": lambda: reminder(parameters=args, response=None, player=None),
                "cmd_control": lambda: cmd_control(parameters=args, player=None),
                "discord_control": lambda: discord_control(parameters=args, player=None),
            }

            handler = tool_map.get(name)
            if handler:
                result = await asyncio.to_thread(handler)
                return str(result or "Done.")

            return f"Tool '{name}' not available in Discord mode"

        except Exception as e:
            return f"Tool error: {e}"


# ═══════════════════════════════════════════════════════════
# DISCORD BOT
# ═══════════════════════════════════════════════════════════

async def run_discord_bot():
    """Main Discord bot entry point."""
    try:
        import discord
        from discord.ext import commands
    except ImportError:
        print("[DiscordBot] ❌ discord.py not installed. Run: pip install discord.py")
        return

    keys = _load_keys()
    token = keys.get("discord_bot_token", "").strip()
    gemini_key = keys.get("gemini_api_key", "").strip()

    if not token:
        print("[DiscordBot] ❌ Discord bot token not found!")
        print("   Add 'discord_bot_token' to config/api_keys.json")
        print("   Create a bot at: https://discord.com/developers/applications")
        return

    if not gemini_key:
        print("[DiscordBot] ❌ Gemini API key not found!")
        print("   Add 'gemini_api_key' to config/api_keys.json")
        return

    # Setup Gemini
    gemini = GeminiChat(gemini_key)

    # Setup Discord bot
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True

    bot = commands.Bot(command_prefix="!jarvis ", intents=intents)
    _last_response: dict[str, float] = {}  # channel_id -> timestamp

    @bot.event
    async def on_ready():
        print(f"[DiscordBot] ✅ Connected as {bot.user}")
        print(f"[DiscordBot] 📡 Servers: {len(bot.guilds)}")
        for guild in bot.guilds:
            print(f"   - {guild.name} ({len(guild.members)} members)")
        await bot.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.listening,
                name="your commands"
            )
        )

    @bot.event
    async def on_message(message: discord.Message):
        # Ignore own messages
        if message.author == bot.user:
            return

        # Cooldown check
        channel_id = str(message.channel.id)
        now = time.time()
        if channel_id in _last_response and now - _last_response[channel_id] < RESPONSE_COOLDOWN:
            return

        should_respond = False

        # DMs — always respond
        if isinstance(message.channel, discord.DMChannel):
            should_respond = True

        # Mentions — respond when @bot is mentioned
        elif bot.user.mentioned_in(message):
            should_respond = True

        # !jarvis command
        elif message.content.strip().lower().startswith("!jarvis"):
            should_respond = True

        if not should_respond:
            # Also process commands
            await bot.process_commands(message)
            return

        _last_response[channel_id] = now

        # Extract the question text
        text = message.content
        # Remove bot mention
        text = text.replace(f"<@{bot.user.id}>", "").strip()
        # Remove !jarvis prefix
        if text.lower().startswith("!jarvis"):
            text = text[7:].strip()

        if not text:
            await message.reply("Oui, monsieur ?")
            return

        # Show typing indicator
        async with message.channel.typing():
            # Get Gemini response (pass channel & bot user for read_channel_history tool)
            response = await gemini.chat(
                text, channel_id,
                discord_channel=message.channel,
                bot_user=bot.user
            )

        # Discord has a 2000 char limit
        if len(response) > MAX_RESPONSE:
            # Split into multiple messages
            chunks = []
            while response:
                if len(response) <= MAX_RESPONSE:
                    chunks.append(response)
                    break
                # Find a good split point
                split_at = response.rfind("\n", 0, MAX_RESPONSE)
                if split_at < MAX_RESPONSE // 2:
                    split_at = MAX_RESPONSE
                chunks.append(response[:split_at])
                response = response[split_at:].strip()

            for i, chunk in enumerate(chunks):
                if i == 0:
                    await message.reply(chunk)
                else:
                    await message.channel.send(chunk)
        else:
            await message.reply(response)

    @bot.command(name="ping")
    async def ping(ctx):
        await ctx.reply("Pong! 🏓")

    @bot.command(name="clear")
    async def clear_history(ctx):
        """Clear conversation history for this channel."""
        channel_id = str(ctx.channel.id)
        if channel_id in gemini._histories:
            gemini._histories[channel_id] = []
        await ctx.reply("Mémoire effacée. 🧹")

    @bot.command(name="servers")
    async def list_servers(ctx):
        """List servers the bot is in."""
        result = "**Serveurs:**\n"
        for g in bot.guilds:
            result += f"• {g.name} ({len(g.members)} membres)\n"
        await ctx.reply(result)

    @bot.command(name="help")
    async def help_cmd(ctx):
        await ctx.reply(
            "**Commandes Jarvis:**\n"
            "• `@Jarvis <question>` — Pose une question\n"
            "• `!jarvis <question>` — Pareil\n"
            "• `!jarvis ping` — Test de latence\n"
            "• `!jarvis clear` — Effacer la mémoire\n"
            "• `!jarvis servers` — Liste des serveurs\n"
            "• `!jarvis help` — Ce message\n\n"
            "Je peux aussi utiliser des outils: recherche web, météo, contrôler Discord, etc.\n"
            "Si tu me demandes de répondre à quelqu'un, je peux lire les derniers messages du salon pour avoir le contexte."
        )

    print("[DiscordBot] 🚀 Starting...")
    try:
        await bot.start(token)
    except discord.LoginFailure:
        print("[DiscordBot] ❌ Invalid bot token. Check config/api_keys.json")
    except Exception as e:
        print(f"[DiscordBot] ❌ Error: {e}")
        traceback.print_exc()


def main():
    """Entry point for standalone execution."""
    print("=" * 50)
    print("  JARVIS — Discord Bot")
    print("=" * 50)
    asyncio.run(run_discord_bot())


if __name__ == "__main__":
    main()

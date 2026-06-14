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

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.model = "gemini-2.5-flash-preview-05-20"
        self.system_prompt = _load_system_prompt()
        # Per-channel conversation history
        self._histories: dict[str, list] = {}
        # Load tool declarations
        self.tools = []
        self._load_tools()

    def _load_tools(self):
        try:
            from main import TOOL_DECLARATIONS
            # Convert to Gemini API format
            self.tools = [{"function_declarations": TOOL_DECLARATIONS}]
        except ImportError:
            print("[DiscordBot] ⚠️ Could not load tool declarations")

    def _get_history(self, channel_id: str) -> list:
        if channel_id not in self._histories:
            self._histories[channel_id] = []
        return self._histories[channel_id]

    def _trim_history(self, channel_id: str):
        hist = self._histories.get(channel_id, [])
        if len(hist) > MAX_HISTORY:
            self._histories[channel_id] = hist[-MAX_HISTORY:]

    async def chat(self, text: str, channel_id: str) -> str:
        """Send a message and get a response using Gemini API."""
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

            # Check for tool calls
            if response.candidates and response.candidates[0].content.parts:
                result_text = ""
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        # Execute tool
                        tool_result = await self._execute_tool(
                            part.function_call.name,
                            dict(part.function_call.args) if part.function_call.args else {}
                        )
                        result_text += f"🔧 **{part.function_call.name}**: {tool_result[:500]}\n"
                    elif part.text:
                        result_text += part.text

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
            # Get Gemini response
            response = await gemini.chat(text, channel_id)

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
            "Je peux aussi utiliser des outils: recherche web, météo, contrôler Discord, etc."
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

<<<<<<< HEAD
"""
discord_control.py — Jarvis Discord Control Action

Lets Jarvis control your Discord via a bot:
- Send messages to channels or users
- Read recent messages from channels
- List servers and channels
- Manage messages (edit, delete, pin)

Setup:
1. Create a Discord bot at https://discord.com/developers/applications
2. Enable MESSAGE CONTENT INTENT in Bot settings
3. Invite bot to your server with permissions:
   Send Messages, Read Message History, Manage Messages
4. Add bot token to config/api_keys.json under "discord_bot_token"
"""

import json
import traceback
from pathlib import Path

_BASE = Path(__file__).resolve().parent.parent
_API_KEYS_PATH = _BASE / "config" / "api_keys.json"


def _get_discord_token() -> str:
    """Load Discord bot token from config."""
    try:
        with open(_API_KEYS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("discord_bot_token", "").strip()
        if not token:
            return ""
        return token
    except Exception:
        return ""


def _get_bot():
    """Get or create the Discord bot client."""
    token = _get_discord_token()
    if not token:
        return None, "Discord bot token not configured. Add 'discord_bot_token' to config/api_keys.json"

    try:
        import discord
    except ImportError:
        return None, "discord.py not installed. Run: pip install discord.py"

    # Reuse existing bot if already running
    if hasattr(_get_bot, '_bot') and _get_bot._bot is not None:
        if not _get_bot._bot.is_closed():
            return _get_bot._bot, None

    # Create new bot instance
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True

    bot = discord.Client(intents=intents)
    _get_bot._bot = bot

    return bot, None

# Cache for the bot instance
_get_bot._bot = None


async def _ensure_ready(bot):
    """Ensure the bot is logged in and ready."""
    if bot.is_ready():
        return True

    token = _get_discord_token()
    if not token:
        return False

    # Try to start the bot
    try:
        await bot.login(token)
        # Start background connection (non-blocking)
        import asyncio
        asyncio.ensure_future(bot.connect())
        # Wait for ready
        for _ in range(50):  # 5 seconds max
            if bot.is_ready():
                return True
            await asyncio.sleep(0.1)
        return bot.is_ready()
    except Exception as e:
        print(f"[Discord] Login failed: {e}")
        return False


async def _send_message(bot, channel_id: int, content: str):
    """Send a message to a channel."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        # Try to fetch it
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None, f"Channel {channel_id} not found"
    try:
        msg = await channel.send(content)
        return msg, None
    except Exception as e:
        return None, str(e)


async def _send_dm(bot, user_id: int, content: str):
    """Send a DM to a user."""
    user = bot.get_user(user_id)
    if user is None:
        try:
            user = await bot.fetch_user(user_id)
        except Exception:
            return None, f"User {user_id} not found"
    try:
        dm = await user.create_dm()
        msg = await dm.send(content)
        return msg, None
    except Exception as e:
        return None, str(e)


async def _read_messages(bot, channel_id: int, limit: int = 10):
    """Read recent messages from a channel."""
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except Exception:
            return None, f"Channel {channel_id} not found"
    try:
        messages = []
        async for msg in channel.history(limit=limit):
            messages.append({
                "author": msg.author.display_name,
                "content": msg.content[:500],
                "time": msg.created_at.strftime("%H:%M"),
            })
        return messages, None
    except Exception as e:
        return None, str(e)


async def _list_channels(bot, guild_name: str = None):
    """List channels in a guild."""
    if not bot.guilds:
        return None, "Bot is not in any server"
    guild = None
    if guild_name:
        for g in bot.guilds:
            if guild_name.lower() in g.name.lower():
                guild = g
                break
    if guild is None:
        guild = bot.guilds[0]

    channels = []
    for ch in guild.text_channels:
        channels.append({
            "name": ch.name,
            "id": ch.id,
            "category": ch.category.name if ch.category else "None",
        })
    return {"server": guild.name, "channels": channels}, None


async def _find_channel(bot, name: str):
    """Find a channel by name across all guilds."""
    name_lower = name.lower().replace(" ", "-")
    for guild in bot.guilds:
        for ch in guild.text_channels:
            if name_lower in ch.name or name.lower() in ch.name.replace("-", " "):
                return ch
    return None


async def _find_user(bot, name: str):
    """Find a user by name."""
    name_lower = name.lower()
    for guild in bot.guilds:
        for member in guild.members:
            if name_lower in member.display_name.lower() or name_lower in member.name.lower():
                return member
    return None


async def _execute_discord_async(action: str, params: dict):
    """Execute a Discord action asynchronously."""
    bot, err = _get_bot()
    if err:
        return err

    if not await _ensure_ready(bot):
        return "Discord bot is not connected. Check your bot token and internet connection."

    # ── SEND MESSAGE ──
    if action == "send_message":
        content = params.get("message", params.get("content", ""))
        if not content:
            return "No message content provided"

        # Try channel name first
        channel_name = params.get("channel", "")
        user_name = params.get("user", params.get("receiver", ""))
        channel_id = params.get("channel_id")

        if user_name:
            user = await _find_user(bot, user_name)
            if user:
                _, err = await _send_dm(bot, user.id, content)
                if err:
                    return f"Failed to send DM to {user.display_name}: {err}"
                return f"DM sent to {user.display_name}"
            return f"User '{user_name}' not found"

        if channel_name:
            ch = await _find_channel(bot, channel_name)
            if ch:
                _, err = await _send_message(bot, ch.id, content)
                if err:
                    return f"Failed to send message: {err}"
                return f"Message sent to #{ch.name}"

        if channel_id:
            _, err = await _send_message(bot, int(channel_id), content)
            if err:
                return f"Failed to send message: {err}"
            return f"Message sent to channel {channel_id}"

        return "Specify a channel name, user name, or channel_id"

    # ── READ MESSAGES ──
    elif action == "read_messages":
        channel_name = params.get("channel", "")
        channel_id = params.get("channel_id")
        limit = int(params.get("limit", 10))

        if channel_name:
            ch = await _find_channel(bot, channel_name)
            if ch:
                channel_id = ch.id
            else:
                return f"Channel '{channel_name}' not found"

        if not channel_id:
            return "Specify a channel name or channel_id"

        messages, err = await _read_messages(bot, int(channel_id), limit)
        if err:
            return f"Failed to read messages: {err}"

        result = f"Last {len(messages)} messages in #{channel_name or channel_id}:\n"
        for m in messages:
            result += f"[{m['time']}] {m['author']}: {m['content']}\n"
        return result.strip()

    # ── LIST CHANNELS ──
    elif action == "list_channels":
        guild_name = params.get("server", "")
        info, err = await _list_channels(bot, guild_name)
        if err:
            return err
        result = f"Server: {info['server']}\nChannels:\n"
        for ch in info['channels']:
            result += f"  #{ch['name']} (id: {ch['id']}) [{ch['category']}]\n"
        return result.strip()

    # ── LIST SERVERS ──
    elif action == "list_servers":
        if not bot.guilds:
            return "Bot is not in any server"
        result = "Servers:\n"
        for g in bot.guilds:
            result += f"  {g.name} ({len(g.text_channels)} channels, {len(g.members)} members)\n"
        return result.strip()

    # ── DELETE MESSAGE ──
    elif action == "delete_message":
        channel_name = params.get("channel", "")
        channel_id = params.get("channel_id")
        message_id = params.get("message_id")
        if not message_id:
            return "Specify message_id to delete"

        if channel_name:
            ch = await _find_channel(bot, channel_name)
            if ch:
                channel_id = ch.id

        if not channel_id:
            return "Specify a channel"

        ch = bot.get_channel(int(channel_id))
        if not ch:
            return "Channel not found"
        try:
            msg = await ch.fetch_message(int(message_id))
            await msg.delete()
            return f"Message {message_id} deleted"
        except Exception as e:
            return f"Failed to delete: {e}"

    # ── EDIT MESSAGE ──
    elif action == "edit_message":
        channel_name = params.get("channel", "")
        channel_id = params.get("channel_id")
        message_id = params.get("message_id")
        new_content = params.get("new_content", params.get("message", ""))
        if not message_id or not new_content:
            return "Specify message_id and new_content"

        if channel_name:
            ch = await _find_channel(bot, channel_name)
            if ch:
                channel_id = ch.id

        if not channel_id:
            return "Specify a channel"

        ch = bot.get_channel(int(channel_id))
        if not ch:
            return "Channel not found"
        try:
            msg = await ch.fetch_message(int(message_id))
            await msg.edit(content=new_content)
            return f"Message {message_id} edited"
        except Exception as e:
            return f"Failed to edit: {e}"

    else:
        return f"Unknown Discord action: {action}. Available: send_message, read_messages, list_channels, list_servers, delete_message, edit_message"


def discord_control(parameters: dict = None, response=None, player=None, session_memory=None) -> str:
    """Main entry point for Jarvis tool dispatch."""
    params = parameters or {}
    action = params.get("action", "list_channels").lower().strip()

    if player:
        player.write_log(f"[Discord] {action}")

    try:
        import asyncio
        # Get or create event loop
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # We're inside an async context (Gemini Live) — create a new loop in a thread
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as executor:
                    future = executor.submit(asyncio.run, _execute_discord_async(action, params))
                    result = future.result(timeout=30)
            else:
                result = loop.run_until_complete(_execute_discord_async(action, params))
        except RuntimeError:
            result = asyncio.run(_execute_discord_async(action, params))

        if player:
            player.write_log(f"[Discord] Done")
        return str(result)

    except ImportError:
        return "discord.py not installed. Run: pip install discord.py"
    except Exception as e:
        err = f"Discord error: {e}"
        print(f"[Discord] ❌ {err}")
        traceback.print_exc()
        if player:
            player.write_log(f"[Discord] ❌ {err}")
        return err
=======
# actions/discord_control.py
# Discord personal account control via self-bot (user token)
# Uses discord.py-self to send messages, read channels, list servers, etc.
# Runs the Discord client in a background thread with its own event loop.

import asyncio
import json
import threading
import time
from pathlib import Path

# Lazy import — discord.py-self may not be installed
_discord = None
_client = None
_ready = threading.Event()
_loop = None
_thread = None

def _get_discord():
    global _discord
    if _discord is None:
        try:
            import discord
            from discord.ext import commands
            _discord = discord
        except ImportError:
            try:
                import selfcord
                _discord = selfcord
            except ImportError:
                raise ImportError(
                    "Neither discord.py-self nor selfcord is installed. "
                    "Run: pip install discord.py-self"
                )
    return _discord


def _get_token() -> str:
    """Read Discord user token from config/api_keys.json."""
    config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        token = data.get("discord_token", "").strip()
        if not token:
            raise ValueError("Discord token not configured. Add 'discord_token' to config/api_keys.json.")
        return token
    except FileNotFoundError:
        raise FileNotFoundError("config/api_keys.json not found.")


class DiscordClientController:
    """Manages a self-bot Discord client in a background thread."""

    def __init__(self):
        self.discord = _get_discord()
        self.client = None
        self.loop = None
        self._ready_event = threading.Event()
        self._connected = False
        self._thread = None
        self._guilds_cache = []
        self._channels_cache = {}

    def start(self):
        """Start the Discord client in a background daemon thread."""
        if self._thread and self._thread.is_alive():
            return  # Already running

        self._thread = threading.Thread(target=self._run_client, daemon=True)
        self._thread.start()
        # Wait up to 15 seconds for the client to be ready
        self._ready_event.wait(timeout=15)

    def _run_client(self):
        """Internal: runs the Discord client event loop."""
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        intents = self.discord.Intents.default()
        intents.message_content = True
        intents.guilds = True
        intents.members = True

        self.client = self.discord.Client(intents=intents)

        @self.client.event
        async def on_ready():
            self._connected = True
            self._guilds_cache = list(self.client.guilds)
            print(f"[Discord] Connected as {self.client.user} — {len(self._guilds_cache)} servers")
            self._ready_event.set()

        @self.client.event
        async def on_disconnect():
            self._connected = False
            print("[Discord] Disconnected")

        try:
            token = _get_token()
            self.loop.run_until_complete(self.client.start(token))
        except Exception as e:
            print(f"[Discord] Client error: {e}")
            self._ready_event.set()  # Unblock even on error

    def is_ready(self) -> bool:
        return self._connected and self.client and not self.client.is_closed()

    def _run_async(self, coro):
        """Run an async coroutine in the client's event loop."""
        if not self.loop or not self.client:
            raise RuntimeError("Discord client not initialized")
        future = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return future.result(timeout=30)

    # ─── Actions ──────────────────────────────────────────────

    def send_dm(self, username: str, message: str) -> str:
        """Send a DM to a user by username or user ID."""
        if not self.is_ready():
            return "Discord client is not connected. Check your token."

        async def _do():
            # Try to find user by name
            user = None

            # Search through mutual guilds
            for guild in self.client.guilds:
                member = guild.get_member_named(username)
                if member:
                    user = member
                    break
                # Also try partial match on display name or name
                for m in guild.members:
                    if (username.lower() in m.name.lower() or
                        username.lower() in m.display_name.lower() or
                        (m.global_name and username.lower() in m.global_name.lower())):
                        user = m
                        break
                if user:
                    break

            # Try by user ID
            if not user and username.isdigit():
                try:
                    user = await self.client.fetch_user(int(username))
                except Exception:
                    pass

            if not user:
                # List available users for helpful error
                known = set()
                for guild in self.client.guilds:
                    for m in guild.members[:20]:
                        name = m.display_name or m.name
                        if name:
                            known.add(name)
                suggestions = ", ".join(list(known)[:15])
                return f"User '{username}' not found. Some users I can see: {suggestions}"

            dm_channel = await user.create_dm()
            await dm_channel.send(message)
            return f"DM sent to {user.display_name or user.name}."

        try:
            return self._run_async(_do())
        except Exception as e:
            return f"Error sending DM: {e}"

    def send_channel_message(self, server_name: str, channel_name: str, message: str) -> str:
        """Send a message to a specific channel in a server."""
        if not self.is_ready():
            return "Discord client is not connected. Check your token."

        async def _do():
            guild = None
            for g in self.client.guilds:
                if server_name.lower() in g.name.lower():
                    guild = g
                    break

            if not guild:
                server_list = ", ".join(g.name for g in self.client.guilds[:15])
                return f"Server '{server_name}' not found. Available: {server_list}"

            channel = None
            for ch in guild.text_channels:
                if channel_name.lower() in ch.name.lower():
                    channel = ch
                    break

            if not channel:
                ch_list = ", ".join(ch.name for ch in guild.text_channels[:20])
                return f"Channel '{channel_name}' not found in '{guild.name}'. Available: {ch_list}"

            await channel.send(message)
            return f"Message sent in #{channel.name} ({guild.name})."

        try:
            return self._run_async(_do())
        except Exception as e:
            return f"Error sending channel message: {e}"

    def read_channel(self, server_name: str, channel_name: str, limit: int = 10) -> str:
        """Read recent messages from a channel."""
        if not self.is_ready():
            return "Discord client is not connected. Check your token."

        async def _do():
            guild = None
            for g in self.client.guilds:
                if server_name.lower() in g.name.lower():
                    guild = g
                    break

            if not guild:
                server_list = ", ".join(g.name for g in self.client.guilds[:15])
                return f"Server '{server_name}' not found. Available: {server_list}"

            channel = None
            for ch in guild.text_channels:
                if channel_name.lower() in ch.name.lower():
                    channel = ch
                    break

            if not channel:
                ch_list = ", ".join(ch.name for ch in guild.text_channels[:20])
                return f"Channel '{channel_name}' not found in '{guild.name}'. Available: {ch_list}"

            messages = []
            async for msg in channel.history(limit=limit):
                author = msg.author.display_name or msg.author.name
                content = msg.content or "(embed/attachment)"
                timestamp = msg.created_at.strftime("%H:%M")
                messages.append(f"[{timestamp}] {author}: {content}")

            if not messages:
                return "No messages found in this channel."

            header = f"Last {len(messages)} messages in #{channel.name} ({guild.name}):\n"
            return header + "\n".join(reversed(messages))

        try:
            return self._run_async(_do())
        except Exception as e:
            return f"Error reading channel: {e}"

    def list_servers(self) -> str:
        """List all servers the user is in."""
        if not self.is_ready():
            return "Discord client is not connected. Check your token."

        lines = []
        for i, guild in enumerate(self.client.guilds, 1):
            member_count = guild.member_count or "?"
            lines.append(f"{i}. {guild.name} ({member_count} members)")

        if not lines:
            return "No servers found."

        return "Your Discord servers:\n" + "\n".join(lines)

    def list_channels(self, server_name: str) -> str:
        """List text channels in a server."""
        if not self.is_ready():
            return "Discord client is not connected. Check your token."

        guild = None
        for g in self.client.guilds:
            if server_name.lower() in g.name.lower():
                guild = g
                break

        if not guild:
            server_list = ", ".join(g.name for g in self.client.guilds[:15])
            return f"Server '{server_name}' not found. Available: {server_list}"

        channels = [f"  #{ch.name}" for ch in guild.text_channels]
        if not channels:
            return f"No text channels found in '{guild.name}'."

        return f"Channels in {guild.name}:\n" + "\n".join(channels)

    def get_status(self) -> str:
        """Get the Discord connection status."""
        if self.is_ready():
            user = self.client.user
            name = user.name if user else "Unknown"
            servers = len(self.client.guilds)
            return f"Connected as {name} — {servers} servers."
        return "Discord client is not connected. Check your token in config."


# ─── Singleton ────────────────────────────────────────────

_controller: DiscordClientController | None = None
_controller_lock = threading.Lock()


def _get_controller() -> DiscordClientController:
    global _controller
    with _controller_lock:
        if _controller is None:
            _controller = DiscordClientController()
        return _controller


def _ensure_started():
    ctrl = _get_controller()
    if not ctrl.is_ready():
        ctrl.start()
    return ctrl


# ─── Public API (called from main.py) ────────────────────

def discord_control(parameters: dict, player=None) -> str:
    """
    Main entry point for Jarvis tool dispatch.

    parameters:
        action      : send_dm | send_channel | read_channel | list_servers | list_channels | status
        receiver    : Username for send_dm
        message     : Message text to send
        server      : Server name for send_channel / read_channel / list_channels
        channel     : Channel name for send_channel / read_channel
        limit       : Number of messages to read (default 10)
    """
    params = parameters or {}
    action = params.get("action", "status").strip().lower()

    print(f"[Discord] Action: {action} | params: {params}")
    if player:
        player.write_log(f"[discord] {action}...")

    try:
        ctrl = _ensure_started()
    except Exception as e:
        return f"Discord setup failed: {e}"

    if action == "send_dm":
        receiver = params.get("receiver", "").strip()
        message = params.get("message", "").strip()
        if not receiver:
            return "Please specify a username to DM."
        if not message:
            return "Please specify a message to send."
        result = ctrl.send_dm(receiver, message)

    elif action == "send_channel":
        server = params.get("server", "").strip()
        channel = params.get("channel", "").strip()
        message = params.get("message", "").strip()
        if not server:
            return "Please specify a server name."
        if not channel:
            return "Please specify a channel name."
        if not message:
            return "Please specify a message to send."
        result = ctrl.send_channel_message(server, channel, message)

    elif action == "read_channel":
        server = params.get("server", "").strip()
        channel = params.get("channel", "").strip()
        limit = int(params.get("limit", 10))
        if not server:
            return "Please specify a server name."
        if not channel:
            return "Please specify a channel name."
        result = ctrl.read_channel(server, channel, limit)

    elif action == "list_servers":
        result = ctrl.list_servers()

    elif action == "list_channels":
        server = params.get("server", "").strip()
        if not server:
            return "Please specify a server name."
        result = ctrl.list_channels(server)

    elif action == "status":
        result = ctrl.get_status()

    else:
        result = f"Unknown Discord action: '{action}'. Available: send_dm, send_channel, read_channel, list_servers, list_channels, status"

    print(f"[Discord] Result: {result[:100]}")
    if player:
        player.write_log(f"[discord] {result[:80]}")

    return result
>>>>>>> b577916 (feat: add Discord personal account control (self-bot))

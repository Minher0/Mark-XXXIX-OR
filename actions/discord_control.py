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
        # Try discord.py-self first (installs as 'discord' module)
        try:
            import discord
            # Verify it's actually discord.py-self and not the regular discord.py
            # discord.py-self has 'user' client support; regular discord.py doesn't
            if not hasattr(discord, 'Intents'):
                raise ImportError(
                    "Wrong 'discord' package detected. "
                    "You have the regular discord.py, not discord.py-self. "
                    "Run: pip uninstall discord.py && pip install discord.py-self"
                )
            # Check if it's the self-bot version by looking for specific attributes
            _discord = discord
        except ImportError:
            pass

        if _discord is None:
            try:
                import selfcord
                _discord = selfcord
            except ImportError:
                raise ImportError(
                    "discord.py-self is not installed. "
                    "Run: pip uninstall discord.py && pip install discord.py-self"
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

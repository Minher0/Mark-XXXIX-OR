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

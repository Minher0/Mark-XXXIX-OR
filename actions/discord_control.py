# actions/discord_control.py
# Discord personal account control via direct HTTP API calls
# Uses only 'requests' — no discord.py-self or selfcord needed
# Works with the user's personal Discord token

import json
import time
from pathlib import Path
from datetime import datetime

import requests

BASE_API = "https://discord.com/api/v10"

# ─── Config ──────────────────────────────────────────────

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


def _headers() -> dict:
    return {
        "Authorization": _get_token(),
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }


def _api_get(endpoint: str, params: dict = None, silent: bool = False) -> dict | list | None:
    """Make a GET request to Discord API. Returns None on failure."""
    try:
        r = requests.get(f"{BASE_API}{endpoint}", headers=_headers(), params=params, timeout=15)
        if r.status_code == 401:
            raise ValueError("Discord token is invalid or expired.")
        if r.status_code == 429:
            retry = r.json().get("retry_after", 5)
            time.sleep(retry)
            r = requests.get(f"{BASE_API}{endpoint}", headers=_headers(), params=params, timeout=15)
        r.raise_for_status()
        return r.json() if r.text else None
    except ValueError:
        raise  # Re-raise auth errors
    except requests.exceptions.RequestException as e:
        if not silent:
            print(f"[Discord] API GET {endpoint} failed: {e}")
        return None


def _api_post(endpoint: str, payload: dict = None) -> dict | list | None:
    """Make a POST request to Discord API."""
    try:
        r = requests.post(f"{BASE_API}{endpoint}", headers=_headers(), json=payload, timeout=15)
        if r.status_code == 401:
            raise ValueError("Discord token is invalid or expired.")
        if r.status_code == 429:
            retry = r.json().get("retry_after", 5)
            time.sleep(retry)
            r = requests.post(f"{BASE_API}{endpoint}", headers=_headers(), json=payload, timeout=15)
        r.raise_for_status()
        return r.json() if r.text else None
    except requests.exceptions.RequestException as e:
        print(f"[Discord] API POST {endpoint} failed: {e}")
        return None


# ─── Cache ───────────────────────────────────────────────

_guilds_cache = None
_guilds_cache_time = 0
CACHE_TTL = 60  # seconds


def _get_guilds() -> list:
    """Get user's guilds with caching."""
    global _guilds_cache, _guilds_cache_time
    if _guilds_cache and (time.time() - _guilds_cache_time) < CACHE_TTL:
        return _guilds_cache
    guilds = _api_get("/users/@me/guilds")
    if guilds is None:
        return []
    _guilds_cache = guilds
    _guilds_cache_time = time.time()
    return guilds


def _find_guild(server_name: str) -> dict | None:
    """Find a guild by name (case-insensitive partial match)."""
    guilds = _get_guilds()
    for g in guilds:
        if server_name.lower() in g.get("name", "").lower():
            return g
    return None


def _find_channel(guild_id: str, channel_name: str) -> dict | None:
    """Find a text channel by name in a guild."""
    channels = _api_get(f"/guilds/{guild_id}/channels")
    if not channels:
        return None
    for ch in channels:
        if ch.get("type", 0) in (0, 5) and channel_name.lower() in ch.get("name", "").lower():
            return ch
    return None


# ─── Actions ─────────────────────────────────────────────

def _status() -> str:
    """Check Discord connection status."""
    try:
        user = _api_get("/users/@me")
        if user:
            name = user.get("username", "Unknown")
            disc = user.get("discriminator", "0")
            guilds = _get_guilds()
            display = f"{name}#{disc}" if disc != "0" else name
            return f"Connected as {display} — {len(guilds)} servers."
        return "Failed to connect. Check your Discord token."
    except ValueError as e:
        return str(e)
    except Exception as e:
        return f"Connection error: {e}"


def _list_servers() -> str:
    """List all Discord servers."""
    guilds = _get_guilds()
    if not guilds:
        return "No servers found, or connection failed."
    lines = []
    for i, g in enumerate(guilds, 1):
        name = g.get("name", "Unknown")
        members = g.get("approximate_member_count", "?")
        lines.append(f"{i}. {name} ({members} members)")
    return "Your Discord servers:\n" + "\n".join(lines)


def _list_channels(server_name: str) -> str:
    """List text channels in a server."""
    guild = _find_guild(server_name)
    if not guild:
        guilds = _get_guilds()
        available = ", ".join(g.get("name", "") for g in guilds[:15])
        return f"Server '{server_name}' not found. Available: {available}"

    channels = _api_get(f"/guilds/{guild['id']}/channels")
    if not channels:
        return f"No channels found in '{guild['name']}'."

    text_channels = [ch for ch in channels if ch.get("type", 0) in (0, 5)]
    lines = [f"  #{ch['name']}" for ch in text_channels]
    if not lines:
        return f"No text channels in '{guild['name']}'."

    return f"Channels in {guild['name']}:\n" + "\n".join(lines)


def _find_user_id(receiver: str) -> str | None:
    """Find a Discord user ID from a name string.
    
    Search order:
    1. If it's a numeric ID, use directly
    2. Check Discord relationships (friends list)
    3. Check existing DM channels
    4. Search guild member lists (paginated)
    """
    def _s(val) -> str:
        """Safe lower — handles None values from API."""
        return (val or "").lower()

    # 1. Numeric ID
    if receiver.isdigit():
        return receiver

    search = receiver.lower().replace("#", "")

    # 2. Check relationships (friends list) — may return 400 for user tokens
    relationships = _api_get("/users/@me/relationships", silent=True)
    if relationships:
        for rel in relationships:
            if rel.get("type") != 1:  # 1 = friend
                continue
            user = rel.get("user", {})
            username = _s(user.get("username"))
            global_name = _s(user.get("global_name"))
            discriminator = user.get("discriminator") or "0"
            full_name = f"{username}#{discriminator}" if discriminator != "0" else username
            if (search in username or
                search in global_name or
                search in full_name.replace("#", "")):
                return user.get("id")

    # 3. Check existing DM channels
    dm_channels = _api_get("/users/@me/channels")
    if dm_channels:
        for ch in dm_channels:
            if ch.get("type") != 1:  # DM type = 1
                continue
            recipients = ch.get("recipients", [])
            for r in recipients:
                username = _s(r.get("username"))
                global_name = _s(r.get("global_name"))
                discriminator = r.get("discriminator") or "0"
                full_name = f"{username}#{discriminator}" if discriminator != "0" else username
                if (search in username or
                    search in global_name or
                    search in full_name.replace("#", "")):
                    return r.get("id")

    # 4. Search guild member lists (paginated, max 100 per page)
    guilds = _get_guilds()
    for g in guilds:
        after = "0"
        for _ in range(10):  # Max 10 pages = 1000 members
            members = _api_get(f"/guilds/{g['id']}/members", params={"limit": 100, "after": after})
            if not members:
                break
            for m in members:
                user = m.get("user", {})
                username = _s(user.get("username"))
                global_name = _s(user.get("global_name"))
                nick = _s(m.get("nick"))
                discriminator = user.get("discriminator") or "0"
                full_name = f"{username}#{discriminator}" if discriminator != "0" else username
                if (search in username or
                    search in global_name or
                    search in nick or
                    search in full_name.replace("#", "")):
                    return user.get("id")
            # Pagination: use last member's ID as 'after'
            last_id = members[-1].get("user", {}).get("id")
            if not last_id:
                break
            after = last_id

    return None


def _send_dm(receiver: str, message: str) -> str:
    """Send a DM to a user."""
    user_id = _find_user_id(receiver)

    if not user_id:
        # Build helpful message with friends and DM contacts
        known = set()
        relationships = _api_get("/users/@me/relationships")
        if relationships:
            for rel in relationships:
                if rel.get("type") != 1:
                    continue
                user = rel.get("user", {})
                name = user.get("username", "")
                disc = user.get("discriminator") or "0"
                if disc != "0":
                    name = f"{name}#{disc}"
                if name:
                    known.add(name)
        if not known:
            dm_channels = _api_get("/users/@me/channels")
            if dm_channels:
                for ch in dm_channels:
                    if ch.get("type") != 1:
                        continue
                    for r in ch.get("recipients", []):
                        name = r.get("username") or ""
                        disc = r.get("discriminator") or "0"
                        if disc != "0":
                            name = f"{name}#{disc}"
                        if name:
                            known.add(name)
        suggestions = ", ".join(list(known)[:15]) if known else ""
        if suggestions:
            return f"User '{receiver}' not found. Your friends/contacts: {suggestions}"
        return f"User '{receiver}' not found. Try their Discord user ID (numeric)."

    # Create DM channel
    dm_channel = _api_post("/users/@me/channels", {"recipient_id": user_id})
    if not dm_channel:
        return f"Could not open DM with user '{receiver}'."

    channel_id = dm_channel.get("id")
    if not channel_id:
        return f"Could not get DM channel ID."

    # Send message
    result = _api_post(f"/channels/{channel_id}/messages", {"content": message})
    if result:
        return f"DM sent to {receiver}."
    return f"Failed to send DM to '{receiver}'."


def _send_channel_message(server_name: str, channel_name: str, message: str) -> str:
    """Send a message to a channel in a server."""
    guild = _find_guild(server_name)
    if not guild:
        guilds = _get_guilds()
        available = ", ".join(g.get("name", "") for g in guilds[:15])
        return f"Server '{server_name}' not found. Available: {available}"

    channel = _find_channel(guild["id"], channel_name)
    if not channel:
        channels = _api_get(f"/guilds/{guild['id']}/channels")
        available = ", ".join(ch.get("name", "") for ch in channels[:20] if ch.get("type", 0) in (0, 5))
        return f"Channel '{channel_name}' not found in '{guild['name']}'. Available: {available}"

    result = _api_post(f"/channels/{channel['id']}/messages", {"content": message})
    if result:
        return f"Message sent in #{channel['name']} ({guild['name']})."
    return f"Failed to send message in #{channel['name']}."


def _read_channel(server_name: str, channel_name: str, limit: int = 10) -> str:
    """Read recent messages from a channel."""
    guild = _find_guild(server_name)
    if not guild:
        guilds = _get_guilds()
        available = ", ".join(g.get("name", "") for g in guilds[:15])
        return f"Server '{server_name}' not found. Available: {available}"

    channel = _find_channel(guild["id"], channel_name)
    if not channel:
        channels = _api_get(f"/guilds/{guild['id']}/channels")
        available = ", ".join(ch.get("name", "") for ch in channels[:20] if ch.get("type", 0) in (0, 5))
        return f"Channel '{channel_name}' not found in '{guild['name']}'. Available: {available}"

    messages = _api_get(f"/channels/{channel['id']}/messages", params={"limit": limit})
    if not messages:
        return "No messages found or could not read channel."

    lines = []
    for msg in reversed(messages):
        author = msg.get("author", {}).get("username", "Unknown")
        content = msg.get("content", "") or "(embed/attachment)"
        timestamp_str = msg.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
            time_fmt = dt.strftime("%H:%M")
        except Exception:
            time_fmt = "?"
        lines.append(f"[{time_fmt}] {author}: {content}")

    header = f"Last {len(lines)} messages in #{channel['name']} ({guild['name']}):\n"
    return header + "\n".join(lines)


# ─── Public API (called from main.py) ────────────────────

def discord_control(parameters: dict, player=None) -> str:
    """
    Main entry point for Jarvis tool dispatch.

    parameters:
        action      : send_dm | send_channel | read_channel | list_servers | list_channels | list_friends | status
        receiver    : Username or user ID for send_dm
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
        if action == "send_dm":
            receiver = params.get("receiver", "").strip()
            message = params.get("message", "").strip()
            if not receiver:
                return "Please specify a username to DM."
            if not message:
                return "Please specify a message to send."
            result = _send_dm(receiver, message)

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
            result = _send_channel_message(server, channel, message)

        elif action == "read_channel":
            server = params.get("server", "").strip()
            channel = params.get("channel", "").strip()
            limit = int(params.get("limit", 10))
            if not server:
                return "Please specify a server name."
            if not channel:
                return "Please specify a channel name."
            result = _read_channel(server, channel, limit)

        elif action == "list_servers":
            result = _list_servers()

        elif action == "list_channels":
            server = params.get("server", "").strip()
            if not server:
                return "Please specify a server name."
            result = _list_channels(server)

        elif action == "list_friends":
            # Try relationships API first (may not work for user tokens)
            relationships = _api_get("/users/@me/relationships", silent=True)
            if relationships:
                friends = []
                for rel in relationships:
                    if rel.get("type") != 1:
                        continue
                    user = rel.get("user", {})
                    name = user.get("username", "?")
                    disc = user.get("discriminator") or "0"
                    gname = user.get("global_name") or ""
                    if disc != "0":
                        display = f"{name}#{disc}"
                    else:
                        display = name
                    if gname and gname != name:
                        display = f"{gname} ({name})"
                    friends.append(display)
                if friends:
                    result = f"Your Discord friends ({len(friends)}):\n" + ", ".join(friends[:50])
                else:
                    result = "No friends found."
            else:
                # Fallback: list DM contacts
                dm_channels = _api_get("/users/@me/channels")
                if dm_channels:
                    contacts = []
                    for ch in dm_channels:
                        if ch.get("type") != 1:
                            continue
                        for r in ch.get("recipients", []):
                            name = r.get("username") or "?"
                            disc = r.get("discriminator") or "0"
                            gname = r.get("global_name") or ""
                            if disc != "0":
                                display = f"{name}#{disc}"
                            else:
                                display = name
                            if gname and gname != name:
                                display = f"{gname} ({name})"
                            contacts.append(display)
                    if contacts:
                        result = f"Your Discord DM contacts ({len(contacts)}):\n" + ", ".join(contacts[:50])
                    else:
                        result = "No Discord contacts found."
                else:
                    result = "Could not fetch Discord contacts."

        elif action == "status":
            result = _status()

        else:
            result = (
                f"Unknown Discord action: '{action}'. "
                "Available: send_dm, send_channel, read_channel, list_servers, list_channels, list_friends, status"
            )

    except ValueError as e:
        result = f"Discord error: {e}"
    except Exception as e:
        result = f"Discord error: {e}"

    print(f"[Discord] Result: {result[:100]}")
    if player:
        player.write_log(f"[discord] {result[:80]}")

    return result

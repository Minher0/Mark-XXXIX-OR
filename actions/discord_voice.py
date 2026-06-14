# actions/discord_voice.py
# Discord Voice Client — Gateway + Voice Connection + Call Management
# Allows Jarvis to see, start, join, and leave Discord voice calls
# using the user's personal Discord token (not a bot).
#
# Architecture:
#   DiscordGateway   → persistent WebSocket to Discord Gateway (heartbeat, events)
#   DiscordVoiceClient → Voice Gateway + UDP transport (encryption, Opus)
#   CallManager      → high-level API (list_calls, start_call, join_call, leave_call)
#
# Optional dependencies:
#   websockets  → required for Gateway + Voice (pip install websockets)
#   pynacl      → required for voice encryption (pip install pynacl)
#   opuslib     → required for audio encoding/decoding (pip install opuslib + opus.dll)

import asyncio
import json
import struct
import threading
import time
import socket
import select
import os
import sys
import traceback
from pathlib import Path
from collections import defaultdict
from typing import Optional, Dict, List

import requests

# ─── Optional Dependencies ─────────────────────────────────────

try:
    import websockets
    HAS_WEBSOCKETS = True
except ImportError:
    HAS_WEBSOCKETS = False

try:
    from nacl.secret import SecretBox
    HAS_NACL = True
except ImportError:
    HAS_NACL = False

try:
    import opuslib
    HAS_OPUS = True
except (ImportError, OSError):
    HAS_OPUS = False

# ─── Config ─────────────────────────────────────────────────────

BASE_API = "https://discord.com/api/v10"


def _get_token() -> str:
    config_path = Path(__file__).resolve().parent.parent / "config" / "api_keys.json"
    with open(config_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    token = data.get("discord_token", "").strip()
    if not token:
        raise ValueError("Discord token not configured")
    return token


def _headers() -> dict:
    return {
        "Authorization": _get_token(),
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    }


# ─── Discord Gateway ────────────────────────────────────────────

GATEWAY_URL = "wss://gateway.discord.gg/?v=10&encoding=json"


class DiscordGateway:
    """Persistent Discord Gateway connection.
    
    Runs in a daemon thread. Tracks voice states across all guilds and DMs.
    Can send VOICE_STATE_UPDATE (op 4) to join/leave voice channels.
    """

    def __init__(self):
        self.token: Optional[str] = None
        self.ws = None
        self.session_id: Optional[str] = None
        self.user_id: Optional[str] = None
        self.username: Optional[str] = None
        self._heartbeat_interval: float = 41.25
        self._sequence: Optional[int] = None
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._connected: bool = False
        self._loop: Optional[asyncio.AbstractEventLoop] = None

        # Voice state tracking:  guild_id → { user_id → voice_state_dict }
        self._voice_states: Dict[str, Dict[str, dict]] = defaultdict(dict)
        # DM voice states:  channel_id → voice_state_dict
        self._dm_voice_states: Dict[str, dict] = {}
        self._lock = threading.Lock()

        # Voice server update (used when joining a call)
        self._pending_voice_server: dict = {}
        self._voice_server_event = threading.Event()

    # ── Lifecycle ──

    def start(self) -> bool:
        if not HAS_WEBSOCKETS:
            print("[Discord GW] ❌ 'websockets' not installed.  pip install websockets")
            return False
        if self._running:
            return True
        try:
            self.token = _get_token()
        except Exception as e:
            print(f"[Discord GW] Token error: {e}")
            return False

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="DiscordGW")
        self._thread.start()

        # Wait up to 10 s for the READY event
        for _ in range(50):
            if self._connected:
                return True
            time.sleep(0.2)
        print("[Discord GW] ⚠️  Gateway not connected after 10 s (thread running)")
        return True  # will connect eventually

    def stop(self):
        self._running = False
        self._connected = False

    def is_connected(self) -> bool:
        return self._connected

    # ── Internal: main loop with reconnection ──

    def _run_loop(self):
        while self._running:
            try:
                asyncio.run(self._connect_and_listen())
            except Exception as e:
                print(f"[Discord GW] Connection error: {e}")
            self._connected = False
            if self._running:
                print("[Discord GW] Reconnecting in 5 s …")
                time.sleep(5)

    async def _connect_and_listen(self):
        async with websockets.connect(GATEWAY_URL, max_size=2 ** 20) as ws:
            self.ws = ws
            self._loop = asyncio.get_event_loop()

            # ── HELLO ──
            hello = json.loads(await ws.recv())
            if hello["op"] != 10:
                raise Exception(f"Expected HELLO (op 10), got op {hello['op']}")
            self._heartbeat_interval = hello["d"]["heartbeat_interval"] / 1000

            # ── IDENTIFY ──
            await ws.send(json.dumps({
                "op": 2,
                "d": {
                    "token": self.token,
                    "properties": {
                        "os": "Windows",
                        "browser": "Chrome",
                        "device": "Chrome",
                    },
                    "compress": False,
                },
            }))

            hb_task = asyncio.create_task(self._heartbeat_loop())

            try:
                async for raw in ws:
                    await self._handle_msg(json.loads(raw))
            except websockets.ConnectionClosed as e:
                print(f"[Discord GW] Connection closed: {e}")
            finally:
                hb_task.cancel()

    async def _heartbeat_loop(self):
        while True:
            await asyncio.sleep(self._heartbeat_interval)
            try:
                await self.ws.send(json.dumps({"op": 1, "d": self._sequence}))
            except Exception:
                break

    # ── Dispatch handler ──

    async def _handle_msg(self, msg: dict):
        op = msg["op"]

        if op == 0:  # DISPATCH
            self._sequence = msg.get("s")
            event = msg.get("t")
            data = msg.get("d", {})

            if event == "READY":
                self.session_id = data["session_id"]
                self.user_id = data["user"]["id"]
                self.username = data["user"]["username"]
                self._connected = True
                print(f"[Discord GW] ✅ Connected as {self.username} (ID {self.user_id})")

                with self._lock:
                    for guild in data.get("guilds", []):
                        gid = guild.get("id")
                        for vs in guild.get("voice_states", []):
                            self._voice_states[gid][vs["user_id"]] = vs

            elif event == "VOICE_STATE_UPDATE":
                guild_id = data.get("guild_id")
                user_id = data.get("user_id")
                channel_id = data.get("channel_id")

                with self._lock:
                    if guild_id:
                        if channel_id:
                            self._voice_states[guild_id][user_id] = data
                        else:
                            self._voice_states[guild_id].pop(user_id, None)
                    else:
                        # DM call
                        if channel_id:
                            self._dm_voice_states[channel_id] = data
                        else:
                            self._dm_voice_states.pop(data.get("channel_id", ""), None)

                if user_id == self.user_id and channel_id:
                    print(f"[Discord GW] We joined voice channel {channel_id}")

            elif event == "VOICE_SERVER_UPDATE":
                self._pending_voice_server = {
                    "guild_id": data.get("guild_id"),
                    "endpoint": data.get("endpoint"),
                    "token": data.get("token"),
                }
                self._voice_server_event.set()

        elif op == 7:  # RECONNECT
            print("[Discord GW] Reconnect requested")
            await self.ws.close()
        elif op == 9:  # INVALID SESSION
            print("[Discord GW] Invalid session — re-identifying")
            self.session_id = None

    # ── Send VOICE_STATE_UPDATE ──

    def send_voice_state_update(self, guild_id: Optional[str] = None,
                                channel_id: Optional[str] = None,
                                self_mute: bool = False,
                                self_deaf: bool = False) -> bool:
        if not self._loop or not self.ws:
            print("[Discord GW] Not connected")
            return False
        payload = {
            "op": 4,
            "d": {
                "guild_id": guild_id,
                "channel_id": channel_id,
                "self_mute": self_mute,
                "self_deaf": self_deaf,
            },
        }
        future = asyncio.run_coroutine_threadsafe(
            self.ws.send(json.dumps(payload)), self._loop
        )
        try:
            future.result(timeout=5)
            return True
        except Exception as e:
            print(f"[Discord GW] Voice state update failed: {e}")
            return False

    def wait_for_voice_server_update(self, timeout: float = 10) -> Optional[dict]:
        self._voice_server_event.clear()
        if self._voice_server_event.wait(timeout):
            return self._pending_voice_server
        return None

    # ── Query helpers ──

    def get_active_voice_channels(self) -> Dict[str, Dict[str, List[dict]]]:
        """Return { guild_id: { channel_id: [voice_state, …] } }."""
        result: Dict[str, Dict[str, List[dict]]] = {}
        with self._lock:
            for guild_id, users in self._voice_states.items():
                channels: Dict[str, List[dict]] = defaultdict(list)
                for user_id, vs in users.items():
                    ch_id = vs.get("channel_id")
                    if ch_id:
                        channels[ch_id].append(vs)
                if channels:
                    result[guild_id] = dict(channels)
        return result

    def get_dm_calls(self) -> Dict[str, dict]:
        with self._lock:
            return dict(self._dm_voice_states)


# ─── Discord Voice Client ───────────────────────────────────────

class DiscordVoiceClient:
    """Full voice connection: Voice Gateway + UDP transport + encryption.
    
    Created by CallManager when joining a call, destroyed on leave.
    """

    def __init__(self, gateway: DiscordGateway):
        self.gateway = gateway
        self.ws = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._running: bool = False
        self._connected: bool = False

        # Voice connection params (set during handshake)
        self.ssrc: Optional[int] = None
        self.ip: Optional[str] = None
        self.port: Optional[int] = None
        self.modes: Optional[list] = None
        self.secret_key: Optional[list] = None
        self._mode: str = ""

        # Audio codec
        self._encoder = None
        self._decoder = None

        # UDP
        self._udp_socket: Optional[socket.socket] = None
        self._sequence: int = 0
        self._timestamp: int = 0

        # Buffers (PCM int16)
        self._mic_buffer: list = []
        self._speaker_buffer: list = []
        self._mic_lock = threading.Lock()
        self._speaker_lock = threading.Lock()

    @property
    def has_audio(self) -> bool:
        return HAS_OPUS and HAS_NACL and self._encoder is not None

    # ── Lifecycle ──

    def connect(self, voice_server_info: dict) -> bool:
        if not HAS_NACL:
            print("[Discord Voice] ❌ PyNaCl not installed.  pip install pynacl")
            return False
        if not HAS_WEBSOCKETS:
            print("[Discord Voice] ❌ websockets not installed")
            return False

        endpoint = voice_server_info["endpoint"]
        if ":" in endpoint:
            endpoint = endpoint.split(":")[0]
        url = f"wss://{endpoint}?v=7"

        self._running = True
        self._thread = threading.Thread(
            target=self._run, args=(url, voice_server_info),
            daemon=True, name="DiscordVoice"
        )
        self._thread.start()

        for _ in range(75):
            if self._connected:
                break
            time.sleep(0.2)
        return self._connected

    def disconnect(self):
        self._running = False
        self._connected = False
        if self._udp_socket:
            try:
                self._udp_socket.close()
            except Exception:
                pass
            self._udp_socket = None
        # Leave voice channel via Gateway
        self.gateway.send_voice_state_update(guild_id=None, channel_id=None)

    # ── Internal: voice gateway thread ──

    def _run(self, url, voice_server_info):
        try:
            asyncio.run(self._connect_voice_gateway(url, voice_server_info))
        except Exception as e:
            print(f"[Discord Voice] Error: {e}")
            traceback.print_exc()
        finally:
            self._connected = False

    async def _connect_voice_gateway(self, url, vsi):
        async with websockets.connect(url, max_size=2 ** 20) as ws:
            self.ws = ws
            self._loop = asyncio.get_event_loop()

            # ── HELLO ──
            hello = json.loads(await ws.recv())
            if hello["op"] != 8:
                raise Exception(f"Expected voice HELLO (op 8), got op {hello['op']}")
            hb_interval = hello["d"]["heartbeat_interval"] / 1000

            # ── IDENTIFY ──
            guild_id = vsi.get("guild_id")
            await ws.send(json.dumps({
                "op": 0,
                "d": {
                    "server_id": guild_id or "0",
                    "user_id": self.gateway.user_id,
                    "session_id": self.gateway.session_id,
                    "token": vsi["token"],
                },
            }))

            # ── READY ──
            ready = json.loads(await ws.recv())
            if ready["op"] != 2:
                raise Exception(f"Expected voice READY (op 2), got op {ready['op']}")
            d = ready["d"]
            self.ssrc = d["ssrc"]
            self.ip = d["ip"]
            self.port = d["port"]
            self.modes = d["modes"]
            print(f"[Discord Voice] READY — ssrc={self.ssrc}  ip={self.ip}:{self.port}  modes={self.modes}")

            # ── IP Discovery ──
            self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._udp_socket.settimeout(5)
            disc_packet = struct.pack(">H", 1) + struct.pack(">H", 70) + struct.pack(">I", self.ssrc) + b"\x00" * 64
            self._udp_socket.sendto(disc_packet, (self.ip, self.port))
            resp = self._udp_socket.recv(256)
            our_ip = resp[8:72].split(b"\x00")[0].decode()
            our_port = struct.unpack(">H", resp[72:76])[0]
            print(f"[Discord Voice] Our address: {our_ip}:{our_port}")

            # ── SELECT_PROTOCOL ──
            chosen_mode = "xsalsa20_poly1305" if "xsalsa20_poly1305" in self.modes else self.modes[0]
            self._mode = chosen_mode
            await ws.send(json.dumps({
                "op": 1,
                "d": {
                    "protocol": "udp",
                    "data": {
                        "address": our_ip,
                        "port": our_port,
                        "mode": chosen_mode,
                    },
                },
            }))

            # ── SESSION_DESCRIPTION ──
            sd = json.loads(await ws.recv())
            if sd["op"] != 4:
                raise Exception(f"Expected SESSION_DESCRIPTION (op 4), got op {sd['op']}")
            self.secret_key = sd["d"]["secret_key"]
            mode = sd["d"]["mode"]
            print(f"[Discord Voice] Session established!  mode={mode}")

            # ── Opus init ──
            if HAS_OPUS:
                try:
                    self._encoder = opuslib.Encoder(48000, 2, opuslib.APPLICATION_AUDIO)
                    self._decoder = opuslib.Decoder(48000, 2)
                    print("[Discord Voice] ✅ Opus encoder/decoder ready")
                except Exception as e:
                    print(f"[Discord Voice] ⚠️  Opus init failed: {e}")
            else:
                print("[Discord Voice] ⚠️  opuslib not installed — no audio.  pip install opuslib")

            self._connected = True

            # ── Run loops ──
            hb_task = asyncio.create_task(self._voice_heartbeat(hb_interval))
            send_task = asyncio.create_task(self._udp_send_loop())
            recv_task = asyncio.create_task(self._udp_recv_loop())
            speak_task = asyncio.create_task(self._speaking_loop())

            try:
                await asyncio.gather(hb_task, send_task, recv_task, speak_task)
            except Exception as e:
                if self._running:
                    print(f"[Discord Voice] Loop error: {e}")
            finally:
                for t in (hb_task, send_task, recv_task, speak_task):
                    t.cancel()

    # ── Heartbeat ──

    async def _voice_heartbeat(self, interval):
        while self._running:
            await asyncio.sleep(interval)
            try:
                await self.ws.send(json.dumps({"op": 3, "d": int(time.time() * 1000)}))
            except Exception:
                break

    # ── Speaking indicator ──

    async def _speaking_loop(self):
        while self._running:
            await asyncio.sleep(1)
            try:
                speaking = 1 if self._mic_buffer else 0
                await self.ws.send(json.dumps({
                    "op": 5,
                    "d": {"speaking": speaking, "delay": 0, "ssrc": self.ssrc},
                }))
            except Exception:
                break

    # ── Encryption ──

    def _encrypt(self, header: bytes, data: bytes) -> bytes:
        """Encrypt audio using xsalsa20_poly1305."""
        key = bytes(self.secret_key)
        box = SecretBox(key)
        nonce = bytearray(24)
        nonce[:12] = header
        encrypted = box.encrypt(data, bytes(nonce))
        return header + encrypted.ciphertext

    def _decrypt(self, packet: bytes) -> Optional[bytes]:
        """Decrypt an incoming UDP voice packet."""
        if len(packet) < 12:
            return None
        header = packet[:12]
        encrypted = packet[12:]
        key = bytes(self.secret_key)
        box = SecretBox(key)
        nonce = bytearray(24)
        nonce[:12] = header
        try:
            return bytes(box.decrypt(encrypted, bytes(nonce)))
        except Exception:
            return None

    # ── UDP send (mic → Discord) ──

    async def _udp_send_loop(self):
        if not self._encoder:
            return  # No audio encoding available

        FRAME_SIZE = 960  # 20 ms at 48 kHz

        while self._running:
            pcm_data = None
            with self._mic_lock:
                if self._mic_buffer:
                    pcm_data = self._mic_buffer.pop(0)

            if not pcm_data:
                await asyncio.sleep(0.01)
                continue

            try:
                # Resample: 16 kHz mono → 48 kHz stereo
                resampled = self._resample_up(pcm_data)

                # Encode Opus
                opus_frame = self._encoder.encode(resampled, FRAME_SIZE)

                # RTP header
                self._sequence = (self._sequence + 1) & 0xFFFF
                self._timestamp = (self._timestamp + FRAME_SIZE) & 0xFFFFFFFF
                header = struct.pack(">HHI", 0x8078, self._sequence, self._timestamp)
                header += struct.pack(">I", self.ssrc)

                # Encrypt + send
                packet = self._encrypt(header, opus_frame)
                self._udp_socket.sendto(packet, (self.ip, self.port))
            except Exception as e:
                if self._running:
                    print(f"[Discord Voice] Send error: {e}")
                await asyncio.sleep(0.05)

    # ── UDP recv (Discord → speaker) ──

    async def _udp_recv_loop(self):
        if not self._decoder:
            return
        if not self._udp_socket:
            return

        self._udp_socket.setblocking(False)

        while self._running:
            try:
                readable, _, _ = select.select([self._udp_socket], [], [], 0.02)
                if not readable:
                    await asyncio.sleep(0.005)
                    continue

                data, _ = self._udp_socket.recvfrom(4096)
                opus_data = self._decrypt(data)
                if not opus_data:
                    continue

                # Decode Opus
                try:
                    pcm_stereo_48k = self._decoder.decode(opus_data, 960)
                except Exception:
                    continue

                # Resample: 48 kHz stereo → 24 kHz mono
                mono_24k = self._resample_down(pcm_stereo_48k)

                with self._speaker_lock:
                    self._speaker_buffer.append(mono_24k)
                    if len(self._speaker_buffer) > 100:
                        self._speaker_buffer = self._speaker_buffer[-50:]

            except Exception:
                if self._running:
                    await asyncio.sleep(0.01)

    # ── Resampling helpers (use numpy if available, else pure-Python) ──

    @staticmethod
    def _resample_up(pcm_int16: bytes) -> bytes:
        """16 kHz mono int16 → 48 kHz stereo int16."""
        try:
            import numpy as _np
            samples = _np.frombuffer(pcm_int16, dtype=_np.int16)
            up = _np.repeat(samples, 3)  # 3× upsample
            stereo = _np.column_stack([up, up]).flatten()
            return stereo.astype(_np.int16).tobytes()
        except ImportError:
            # Pure-Python fallback (slow but works)
            import array
            a = array.array("h", pcm_int16)
            out = array.array("h")
            for s in a:
                for _ in range(3):
                    out.append(s)
                    out.append(s)
            return out.tobytes()

    @staticmethod
    def _resample_down(pcm_bytes: bytes) -> bytes:
        """48 kHz stereo int16 → 24 kHz mono int16."""
        try:
            import numpy as _np
            stereo = _np.frombuffer(pcm_bytes, dtype=_np.int16)
            mono = stereo[::2]        # left channel only
            down = mono[::2]          # 2× downsample
            return down.astype(_np.int16).tobytes()
        except ImportError:
            import array
            a = array.array("h", pcm_bytes)
            out = array.array("h")
            for i in range(0, len(a), 4):
                out.append(a[i])
            return out.tobytes()

    # ── Public: audio I/O (called from Jarvis pipeline) ──

    def push_mic_audio(self, pcm_chunk: bytes):
        """Add mic PCM (16 kHz mono int16) to the Discord send buffer."""
        if not self._connected:
            return
        with self._mic_lock:
            self._mic_buffer.append(pcm_chunk)
            if len(self._mic_buffer) > 100:
                self._mic_buffer = self._mic_buffer[-50:]

    def pop_speaker_audio(self) -> Optional[bytes]:
        """Get decoded PCM (24 kHz mono int16) from Discord."""
        with self._speaker_lock:
            if self._speaker_buffer:
                return self._speaker_buffer.pop(0)
        return None

    def has_speaker_audio(self) -> bool:
        with self._speaker_lock:
            return len(self._speaker_buffer) > 0


# ─── Call Manager ────────────────────────────────────────────────

class CallManager:
    """High-level Discord call management singleton."""

    _instance: Optional["CallManager"] = None

    @classmethod
    def get(cls) -> "CallManager":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.gateway = DiscordGateway()
        self.voice_client: Optional[DiscordVoiceClient] = None
        self._in_call: bool = False
        self._current_channel_id: Optional[str] = None
        self._current_guild_id: Optional[str] = None

    # ── Lifecycle ──

    def start(self) -> bool:
        return self.gateway.start()

    def stop(self):
        self.leave_call()
        self.gateway.stop()

    def is_in_call(self) -> bool:
        return self._in_call

    # ── List active calls ──

    def list_calls(self) -> str:
        if not self.gateway.is_connected():
            return "Discord Gateway not connected. Voice features unavailable."

        lines: list = []

        # ── DM calls ──
        dm_calls = self.gateway.get_dm_calls()
        if dm_calls:
            # Enrich with user names from REST API
            dm_contacts = self._get_dm_contacts()
            lines.append("Appels MP en cours :")
            for ch_id, vs in dm_calls.items():
                user_id = vs.get("user_id", "?")
                name = dm_contacts.get(ch_id, f"User {user_id}")
                # Don't list ourselves
                if user_id == self.gateway.user_id:
                    name = dm_contacts.get(ch_id, f"Channel {ch_id}")
                lines.append(f"  • {name}")

        # ── Guild voice channels ──
        active = self.gateway.get_active_voice_channels()
        if active:
            guild_names = self._get_guild_names()
            for guild_id, channels in active.items():
                gname = guild_names.get(guild_id, f"Serveur {guild_id}")
                lines.append(f"🔊 {gname} :")
                for ch_id, users in channels.items():
                    ch_name = self._get_channel_name(ch_id)
                    user_ids = [u.get("user_id", "?") for u in users
                                if u.get("user_id") != self.gateway.user_id]
                    if user_ids:
                        names = self._resolve_usernames(user_ids)
                        lines.append(f"  • #{ch_name} — {', '.join(names)}")

        if not lines:
            return "Aucun appel vocal Discord en cours."

        return "Appels Discord actifs :\n" + "\n".join(lines)

    # ── Start / ring a DM call ──

    def start_call(self, receiver: str) -> str:
        from actions.discord_control import _find_user_id

        user_id = _find_user_id(receiver)
        if not user_id:
            return f"Utilisateur '{receiver}' introuvable sur Discord."

        # Open DM channel
        try:
            r = requests.post(f"{BASE_API}/users/@me/channels",
                              headers=_headers(), json={"recipient_id": user_id}, timeout=10)
            r.raise_for_status()
            dm_channel = r.json()
            channel_id = dm_channel["id"]
        except Exception as e:
            return f"Impossible d'ouvrir un MP avec '{receiver}' : {e}"

        # Ring
        try:
            r = requests.post(f"{BASE_API}/channels/{channel_id}/call",
                              headers=_headers(), json={}, timeout=10)
            if r.status_code in (200, 204):
                return f"Appel lancé vers {receiver} !"
            return f"Sonnerie envoyée à {receiver} (status {r.status_code})."
        except Exception as e:
            return f"Impossible d'appeler {receiver} : {e}"

    # ── Join a call ──

    def join_call(self, receiver_or_channel: str) -> str:
        if not self.gateway.is_connected():
            return "Discord Gateway non connecté. Impossible de rejoindre un appel."
        if self._in_call:
            return "Déjà en appel. Quitte l'appel actuel d'abord."
        if not HAS_NACL:
            return "PyNaCl non installé. Installe-le avec : pip install pynacl"

        from actions.discord_control import _find_user_id

        channel_id: Optional[str] = None
        guild_id: Optional[str] = None

        # Try to find as a user first
        user_id = _find_user_id(receiver_or_channel)
        if user_id:
            # Open DM channel
            try:
                r = requests.post(f"{BASE_API}/users/@me/channels",
                                  headers=_headers(), json={"recipient_id": user_id}, timeout=10)
                r.raise_for_status()
                dm_channel = r.json()
                channel_id = dm_channel["id"]
                guild_id = None
            except Exception as e:
                return f"Impossible d'ouvrir un MP avec '{receiver_or_channel}' : {e}"
        elif receiver_or_channel.isdigit():
            # Maybe a channel ID
            channel_id = receiver_or_channel
            try:
                ch_info = requests.get(f"{BASE_API}/channels/{channel_id}",
                                       headers=_headers(), timeout=5).json()
                if isinstance(ch_info, dict):
                    guild_id = ch_info.get("guild_id")
                    ch_type = ch_info.get("type", 0)
                    if ch_type not in (1, 2, 3, 13):
                        return f"Le channel {channel_id} n'est pas un channel vocal (type={ch_type})."
            except Exception:
                pass
        else:
            return f"Utilisateur ou channel '{receiver_or_channel}' introuvable."

        if not channel_id:
            return f"Impossible de déterminer le channel pour '{receiver_or_channel}'."

        # Send VOICE_STATE_UPDATE
        print(f"[Discord Call] Joining channel {channel_id} (guild={guild_id})")
        self.gateway.send_voice_state_update(guild_id=guild_id, channel_id=channel_id)

        # Wait for VOICE_SERVER_UPDATE
        voice_info = self.gateway.wait_for_voice_server_update(timeout=10)
        if not voice_info:
            self.gateway.send_voice_state_update(guild_id=None, channel_id=None)
            return "Timeout en attendant les infos du serveur vocal. Impossible de rejoindre l'appel."

        # Connect voice client
        self.voice_client = DiscordVoiceClient(self.gateway)
        if self.voice_client.connect(voice_info):
            self._in_call = True
            self._current_channel_id = channel_id
            self._current_guild_id = guild_id
            audio_ok = self.voice_client.has_audio
            status = "avec audio" if audio_ok else "sans audio (installe opuslib pour l'audio)"
            return f"Appel rejoint {status}. Dis « quitte l'appel » pour sortir."
        else:
            self.gateway.send_voice_state_update(guild_id=None, channel_id=None)
            return "Échec de connexion au serveur vocal."

    # ── Leave call ──

    def leave_call(self) -> str:
        if not self._in_call:
            return "Pas en appel actuellement."

        if self.voice_client:
            self.voice_client.disconnect()
            self.voice_client = None

        self.gateway.send_voice_state_update(
            guild_id=self._current_guild_id, channel_id=None
        )
        self._in_call = False
        self._current_channel_id = None
        self._current_guild_id = None
        return "Appel quitté."

    # ── Audio bridge ──

    def push_mic_audio(self, pcm_chunk: bytes):
        if self.voice_client and self._in_call:
            self.voice_client.push_mic_audio(pcm_chunk)

    def pop_speaker_audio(self) -> Optional[bytes]:
        if self.voice_client and self._in_call:
            return self.voice_client.pop_speaker_audio()
        return None

    def has_speaker_audio(self) -> bool:
        if self.voice_client and self._in_call:
            return self.voice_client.has_speaker_audio()
        return False

    # ── REST API helpers ──

    def _get_guild_names(self) -> Dict[str, str]:
        try:
            r = requests.get(f"{BASE_API}/users/@me/guilds", headers=_headers(), timeout=10)
            guilds = r.json()
            if isinstance(guilds, list):
                return {g["id"]: g["name"] for g in guilds if isinstance(g, dict)}
        except Exception:
            pass
        return {}

    def _get_dm_contacts(self) -> Dict[str, str]:
        """Return { dm_channel_id: display_name }."""
        try:
            r = requests.get(f"{BASE_API}/users/@me/channels", headers=_headers(), timeout=10)
            channels = r.json()
            if not isinstance(channels, list):
                return {}
            result = {}
            for ch in channels:
                if ch.get("type") not in (1, 3):
                    continue
                recipients = ch.get("recipients", [])
                names = []
                for u in recipients:
                    uname = u.get("username", "?")
                    disc = u.get("discriminator", "0")
                    gname = u.get("global_name", "")
                    if gname and gname != uname:
                        names.append(f"{gname} ({uname})")
                    elif disc != "0":
                        names.append(f"{uname}#{disc}")
                    else:
                        names.append(uname)
                result[ch["id"]] = ", ".join(names) if names else ch["id"]
            return result
        except Exception:
            return {}

    def _get_channel_name(self, channel_id: str) -> str:
        try:
            r = requests.get(f"{BASE_API}/channels/{channel_id}", headers=_headers(), timeout=5)
            ch = r.json()
            if isinstance(ch, dict):
                return ch.get("name") or ch.get("id", channel_id)
        except Exception:
            pass
        return channel_id

    def _resolve_usernames(self, user_ids: list) -> list:
        names = []
        for uid in user_ids[:10]:
            try:
                r = requests.get(f"{BASE_API}/users/{uid}", headers=_headers(), timeout=5)
                u = r.json()
                if isinstance(u, dict):
                    names.append(u.get("username", uid))
                else:
                    names.append(uid)
            except Exception:
                names.append(uid)
        return names


# ─── Module-level convenience ────────────────────────────────────

def get_call_manager() -> CallManager:
    return CallManager.get()

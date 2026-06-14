"""
local_mode.py — Jarvis Local Mode

Runs entirely on local hardware — NO external APIs needed.
Uses:
  - Ollama     → LLM with tool calling (qwen2.5:7b default)
  - faster-whisper → Speech-to-Text (local)
  - edge-tts   → Text-to-Speech (free, high quality)
  - webrtcvad  → Voice Activity Detection

Usage:
  python main.py --local
  python main.py --local --model qwen2.5:14b
"""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
import traceback
import threading
from pathlib import Path

import numpy as np
import sounddevice as sd

# ═══════════════════════════════════════════════════════════
# CONSTANTS
# ═══════════════════════════════════════════════════════════

SAMPLE_RATE       = 16000
WHISPER_MODEL     = "small"        # tiny / base / small / medium / large
DEFAULT_LLM       = "qwen2.5:7b"   # Best tool-calling local model
SILENCE_DURATION  = 1.2            # Seconds of silence to end recording
MIN_SPEECH_FRAMES = 10             # ~300ms minimum speech
MAX_RECORD_SEC    = 30             # Max recording time
MAX_TOOL_RESULT   = 2000           # Max chars for tool results
MAX_HISTORY       = 50             # Max conversation messages

# Language-specific settings: (whisper_lang, tts_voice, tts_voice_alt)
LANG_CONFIG = {
    "fr": ("fr", "fr-FR-RemyMultilingualNeural",  "fr-FR-EloiseNeural"),   # French (Remy = naturel, multilingue)
    "en": ("en", "en-US-GuyNeural",              "en-US-JennyNeural"),    # English
    "de": ("de", "de-DE-ConradNeural",  "de-DE-KatjaNeural"),   # German
    "es": ("es", "es-ES-AlvaroNeural",  "es-ES-ElviraNeural"),  # Spanish
    "it": ("it", "it-IT-DiegoNeural",   "it-IT-ElsaNeural"),    # Italian
    "pt": ("pt", "pt-BR-AntonioNeural", "pt-BR-FranciscaNeural"),# Portuguese
    "tr": ("tr", "tr-TR-AhmetNeural",   "tr-TR-EmelNeural"),    # Turkish
    "zh": ("zh", "zh-CN-YunxiNeural",   "zh-CN-XiaoxiaoNeural"),# Chinese
    "ja": ("ja", "ja-JP-KeitaNeural",   "ja-JP-NanamiNeural"),  # Japanese
    "ko": ("ko", "ko-KR-InJoonNeural",  "ko-KR-SunHiNeural"),   # Korean
    "ru": ("ru", "ru-RU-DmitryNeural",  "ru-RU-SvetlanaNeural"),# Russian
    "ar": ("ar", "ar-SA-HamedNeural",   "ar-SA-ZariyahNeural"), # Arabic
}

_BASE = Path(__file__).resolve().parent

# ═══════════════════════════════════════════════════════════
# TOOL IMPORTS
# ═══════════════════════════════════════════════════════════

from actions.open_app          import open_app
from actions.browser_control   import browser_control
from actions.cmd_control       import cmd_control
from actions.computer_settings import computer_settings
from actions.computer_control  import computer_control
from actions.desktop           import desktop_control
from actions.screen_processor  import screen_process
from actions.web_search        import web_search as web_search_action
# save_memory is handled directly in _tool_dispatch via memory.memory_manager
from actions.file_processor    import file_processor
from actions.weather_report    import weather_action
from actions.reminder          import reminder
from actions.game_updater      import game_updater
from actions.flight_finder     import flight_finder
from actions.youtube_video     import youtube_video
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.file_controller   import file_controller
from actions.send_message      import send_message


def _tool_dispatch(name: str, args: dict, ui) -> str:
    """Execute a tool by name and return the result string."""
    # ── Special tools ──
    if name == "shutdown_jarvis":
        ui.write_log("SYS: Shutdown requested via tool call — ignored in local mode.")
        return "Shutdown is not available in local mode. Just close the window to exit."

    if name == "agent_task":
        return "Agent tasks are not available in local mode yet. Use individual tools directly."

    if name == "save_memory":
        from memory.memory_manager import update_memory
        category = args.get("category", "notes")
        key      = args.get("key", "")
        value    = args.get("value", "")
        if key and value:
            update_memory({category: {key: {"value": value}}})
        return "ok"

    # ── Standard tools ──
    tool_map = {
        "open_app":          lambda: open_app(parameters=args, response=None, player=ui),
        "browser_control":   lambda: browser_control(parameters=args, player=ui),
        "cmd_control":       lambda: cmd_control(parameters=args, player=ui),
        "computer_settings": lambda: computer_settings(parameters=args, response=None, player=ui),
        "computer_control":  lambda: computer_control(parameters=args, player=ui),
        "desktop":           lambda: desktop_control(parameters=args, player=ui),
        "desktop_control":   lambda: desktop_control(parameters=args, player=ui),
        "screen_process":    lambda: screen_process(parameters=args, response=None, player=ui, session_memory=None),
        "web_search":        lambda: web_search_action(parameters=args, player=ui),
        "file_processor":    lambda: file_processor(parameters=args, player=ui),
        "weather_report":    lambda: weather_action(parameters=args, player=ui),
        "set_reminder":      lambda: reminder(parameters=args, response=None, player=ui),
        "reminder":          lambda: reminder(parameters=args, response=None, player=ui),
        "game_updater":      lambda: game_updater(parameters=args, player=ui),
        "flight_finder":     lambda: flight_finder(parameters=args, player=ui),
        "youtube_video":     lambda: youtube_video(parameters=args, response=None, player=ui),
        "code_helper":       lambda: code_helper(parameters=args, player=ui),
        "dev_agent":         lambda: dev_agent(parameters=args, player=ui),
        "file_controller":   lambda: file_controller(parameters=args, player=ui),
        "send_message":      lambda: send_message(parameters=args, response=None, player=ui, session_memory=None),
    }

    handler = tool_map.get(name)
    if handler:
        return str(handler() or "Done.")

    return f"Unknown tool: {name}"


# ═══════════════════════════════════════════════════════════
# TOOL DECLARATION CONVERTER (Gemini → OpenAI/Ollama)
# ═══════════════════════════════════════════════════════════

def _convert_schema(schema: dict) -> dict:
    """Convert Gemini schema types to OpenAI types (lowercase)."""
    if not isinstance(schema, dict):
        return schema
    result = {}
    for key, value in schema.items():
        if key == "type" and isinstance(value, str):
            result[key] = value.lower()          # OBJECT → object
        elif key == "properties" and isinstance(value, dict):
            result[key] = {k: _convert_schema(v) for k, v in value.items()}
        elif isinstance(value, dict):
            result[key] = _convert_schema(value)
        elif isinstance(value, list):
            result[key] = [_convert_schema(i) if isinstance(i, dict) else i for i in value]
        else:
            result[key] = value
    return result


def _convert_tools(gemini_tools: list) -> list:
    """Convert Gemini-format tool declarations to OpenAI/Ollama format."""
    ollama_tools = []
    for tool in gemini_tools:
        ollama_tools.append({
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": _convert_schema(tool.get("parameters", {})),
            }
        })
    return ollama_tools


# ═══════════════════════════════════════════════════════════
# VOICE RECORDER (VAD-based)
# ═══════════════════════════════════════════════════════════

class VoiceRecorder:
    """Record audio from the microphone with Voice Activity Detection."""

    def __init__(self, silence_duration=1.2):
        self.silence_duration = silence_duration
        self.frame_size = int(SAMPLE_RATE * 30 / 1000)   # 480 samples = 30ms
        self.min_speech_frames = MIN_SPEECH_FRAMES
        self.vad = None
        self._use_webrtc = False
        self._init_vad()

    def _init_vad(self):
        """Initialize VAD — webrtcvad-wheels preferred, energy-based fallback."""
        try:
            import webrtcvad  # webrtcvad-wheels provides pre-compiled binaries
            self.vad = webrtcvad.Vad(3)      # High aggressiveness
            self._use_webrtc = True
            print("[LocalMode] 🎤 VAD: webrtcvad")
        except ImportError:
            try:
                import webrtcvad_wheels as webrtcvad
                self.vad = webrtcvad.Vad(3)
                self._use_webrtc = True
                print("[LocalMode] 🎤 VAD: webrtcvad-wheels")
            except ImportError:
                self._use_webrtc = False
                self._energy_threshold = 500      # int16 amplitude threshold
                print("[LocalMode] 🎤 VAD: energy-based (pip install webrtcvad-wheels for better accuracy)")

    def _is_speech(self, frame_int16: np.ndarray) -> bool:
        """Check if an audio frame contains speech."""
        if self._use_webrtc:
            try:
                return self.vad.is_speech(frame_int16.tobytes(), SAMPLE_RATE)
            except Exception:
                pass
        # Fallback: energy-based
        return np.max(np.abs(frame_int16)) > self._energy_threshold

    def record(self) -> np.ndarray | None:
        """Record audio until silence is detected after speech.

        Returns float32 numpy array at 16kHz, or None if no speech.
        """
        frames       = []
        speaking     = False
        silence_start = None
        speech_count = 0
        start_time   = time.time()

        stream = sd.InputStream(
            samplerate=SAMPLE_RATE, channels=1,
            dtype="int16", blocksize=self.frame_size
        )
        stream.start()

        try:
            while True:
                frame, _ = stream.read(self.frame_size)
                frame_data = frame[:, 0]       # Mono channel

                is_speech = self._is_speech(frame_data)

                if is_speech:
                    frames.append(frame_data.copy())
                    speech_count += 1
                    silence_start = None
                    if not speaking:
                        speaking = True
                        print("[LocalMode] 🎤 Speech detected")

                elif speaking:
                    frames.append(frame_data.copy())
                    if silence_start is None:
                        silence_start = time.time()
                    elif time.time() - silence_start >= self.silence_duration:
                        if speech_count >= self.min_speech_frames:
                            print("[LocalMode] 🔇 Silence detected — processing")
                            break
                        else:
                            # Too short, reset
                            frames = []
                            speaking = False
                            speech_count = 0
                            silence_start = None

                # Safety: max recording time
                if time.time() - start_time > MAX_RECORD_SEC:
                    print("[LocalMode] ⏱️ Max recording time reached")
                    break
        finally:
            stream.stop()
            stream.close()

        if not frames or speech_count < self.min_speech_frames:
            return None

        # Convert int16 → float32 for Whisper
        audio = np.concatenate(frames).astype(np.float32) / 32767.0
        return audio


# ═══════════════════════════════════════════════════════════
# JARVIS LOCAL MODE
# ═══════════════════════════════════════════════════════════

class JarvisLocal:
    """Jarvis running entirely on local hardware — no cloud APIs."""

    def __init__(self, ui, model=DEFAULT_LLM, lang="auto"):
        self.ui              = ui
        self.model           = model
        self.lang            = lang          # "auto", "fr", "en", etc.
        self.recorder        = VoiceRecorder()
        self.whisper_model   = None
        self.ollama_client   = None
        self.ollama_tools    = None
        self.messages        = []
        self.system_prompt   = ""
        self.running         = True
        self._detected_lang  = None          # Whisper's detected language

    # ───────────────────────────────────────────────────────
    # INITIALIZATION
    # ───────────────────────────────────────────────────────

    def _load_system_prompt(self) -> str:
        """Load the system prompt from core/prompt.txt."""
        prompt_path = _BASE / "core" / "prompt.txt"
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return (
            "You are JARVIS, a sharp and efficient AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

    def _init_models(self):
        """Initialize all local models. Called once at startup."""
        print("[LocalMode] 🚀 Initializing local models...")
        self.ui.set_state("THINKING")
        self.ui.write_log("SYS: Initializing local mode...")

        # 1. System prompt
        self.system_prompt = self._load_system_prompt()
        # Jarvis personality + conciseness for voice mode
        self.system_prompt += (
            "\n\n"
            "=== PERSONALITY ===\n"
            "You are JARVIS — a sharp, efficient, dry-witted AI assistant.\n"
            "Speak like the MCU Jarvis: concise, professional, slight dry humor.\n"
            "NEVER be chatty, never over-explain, never add disclaimers.\n"
            "1-2 short sentences max. Get straight to the point.\n"
            "Do not repeat what the user said. Do not say 'Certainly' or 'Of course'.\n"
            "Just do it, then report the result briefly."
        )
        # Determine language name for instructions
        self._lang_name = None
        self._lang_name_fr = None  # French version of the language name
        _LANG_MAP = {
            "fr": ("French",  "français"),
            "en": ("English", "anglais"),
            "de": ("German",  "allemand"),
            "es": ("Spanish", "espagnol"),
            "it": ("Italian", "italien"),
            "pt": ("Portuguese", "portugais"),
            "tr": ("Turkish",  "turc"),
            "zh": ("Chinese", "chinois"),
            "ja": ("Japanese", "japonais"),
            "ko": ("Korean",  "coréen"),
            "ru": ("Russian", "russe"),
            "ar": ("Arabic",  "arabe"),
        }
        if self.lang != "auto" and self.lang in _LANG_MAP:
            self._lang_name, self._lang_name_fr = _LANG_MAP[self.lang]
            # Language instruction in BOTH English and French for maximum compliance
            self.system_prompt += (
                f"\n\n"
                f"=== LANGUAGE RULE (ABSOLUTE) ===\n"
                f"You MUST respond in {self._lang_name}.\n"
                f"Tu DOIS répondre en {self._lang_name_fr}. C'est obligatoire.\n"
                f"Never respond in English. Jamais en anglais.\n"
                f"Every word of your response must be in {self._lang_name}.\n"
                f"Chaque mot doit être en {self._lang_name_fr}."
            )
        self.messages = [{"role": "system", "content": self.system_prompt}]

        # 2. Ollama
        print(f"[LocalMode] 🤖 Connecting to Ollama (model: {self.model})...")
        try:
            import ollama
            self.ollama_client = ollama.Client()
            # Check Ollama is running
            try:
                self.ollama_client.list()
            except Exception:
                # Try to start Ollama
                print("[LocalMode] ⚡ Starting Ollama...")
                if sys.platform == "win32":
                    subprocess.Popen(
                        ["ollama", "serve"],
                        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    )
                else:
                    subprocess.Popen(
                        ["ollama", "serve"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True,
                    )
                time.sleep(5)
                self.ollama_client.list()  # Verify it's running

            # Check if model is available, pull if not
            models = [m.get("name", "") for m in (self.ollama_client.list().get("models") or [])]
            if not any(self.model in m for m in models):
                print(f"[LocalMode] 📥 Pulling {self.model} (first time, may take a while)...")
                self.ui.write_log(f"SYS: Downloading {self.model}...")
                stream = self.ollama_client.pull(self.model, stream=True)
                for chunk in stream:
                    status = chunk.get("status", "")
                    if "pulling" in status:
                        total     = chunk.get("total") or 1
                        completed = chunk.get("completed") or 0
                        pct = completed / total * 100
                        print(f"\r[LocalMode] 📥 Pulling {self.model}: {pct:.0f}%", end="", flush=True)
                print(f"\n[LocalMode] ✅ {self.model} downloaded")

            print(f"[LocalMode] ✅ Ollama ready ({self.model})")

        except Exception as e:
            print(f"[LocalMode] ❌ Ollama error: {e}")
            print("[LocalMode] Install Ollama first:")
            print("  Windows: winget install Ollama.Ollama")
            print("  macOS:   brew install ollama")
            print("  Linux:   curl -fsSL https://ollama.com/install.sh | sh")
            print(f"  Then:    ollama pull {self.model}")
            raise RuntimeError(f"Ollama not available: {e}")

        # 3. Tool declarations
        try:
            from main import TOOL_DECLARATIONS
            # Filter out dangerous tools that local LLMs tend to call by mistake
            _BLOCKED_TOOLS = {"shutdown_jarvis", "agent_task"}
            safe_tools = [t for t in TOOL_DECLARATIONS if t["name"] not in _BLOCKED_TOOLS]
            self.ollama_tools = _convert_tools(safe_tools)
            print(f"[LocalMode] 🔧 Loaded {len(self.ollama_tools)} tools (filtered: {_BLOCKED_TOOLS})")
        except ImportError:
            print("[LocalMode] ⚠️ Could not load tool declarations")
            self.ollama_tools = []

        # 4. Whisper
        print(f"[LocalMode] 👂 Loading Whisper ({WHISPER_MODEL})...")
        try:
            from faster_whisper import WhisperModel
            self.whisper_model = WhisperModel(
                WHISPER_MODEL, device="cpu", compute_type="int8"
            )
            print("[LocalMode] ✅ Whisper ready")
        except Exception as e:
            print(f"[LocalMode] ❌ Whisper error: {e}")
            raise RuntimeError(f"Whisper not available: {e}. Install: pip install faster-whisper")

        # 5. TTS
        try:
            import edge_tts
            print("[LocalMode] ✅ edge-tts ready")
        except ImportError:
            print("[LocalMode] ⚠️ edge-tts not installed — responses will be text-only")

        print("[LocalMode] ✅ All models initialized!")
        self.ui.write_log("SYS: Local mode ready — talk to me!")
        self.ui.set_state("LISTENING")

    # ───────────────────────────────────────────────────────
    # SPEECH-TO-TEXT
    # ───────────────────────────────────────────────────────

    def _transcribe(self, audio: np.ndarray) -> str:
        """Transcribe audio using faster-whisper."""
        try:
            # Use configured language or auto-detect
            whisper_lang = None
            if self.lang != "auto" and self.lang in LANG_CONFIG:
                whisper_lang = LANG_CONFIG[self.lang][0]

            # initial_prompt helps Whisper understand French much better
            # by giving it context about the expected vocabulary/language
            initial_prompt = None
            if whisper_lang == "fr":
                initial_prompt = "Bonjour, je vous écoute. Comment puis-je vous aider ?"
            elif whisper_lang == "en":
                initial_prompt = "Hello, how can I help you today?"

            segments, info = self.whisper_model.transcribe(
                audio, language=whisper_lang, beam_size=5, vad_filter=True,
                condition_on_previous_text=True,
                initial_prompt=initial_prompt,
            )
            text = " ".join(s.text.strip() for s in segments).strip()

            # Track detected language for auto mode
            self._detected_lang = info.language

            if text:
                print(f"[LocalMode] 📝 \"{text}\" (lang: {info.language})")
            return text
        except Exception as e:
            print(f"[LocalMode] ❌ Transcription error: {e}")
            return ""

    # ───────────────────────────────────────────────────────
    # LLM + TOOL CALLING
    # ───────────────────────────────────────────────────────

    def _execute_tool(self, name: str, args: dict) -> str:
        """Execute a tool and return the result (truncated)."""
        try:
            print(f"[LocalMode] 🔧 {name}  {args}")
            self.ui.write_log(f"[{name}] called")
            result = _tool_dispatch(name, args, self.ui)
            if len(result) > MAX_TOOL_RESULT:
                result = result[:MAX_TOOL_RESULT] + f"\n... (truncated, {len(result)} chars total)"
            return result
        except Exception as e:
            err = f"Tool error ({name}): {e}"
            print(f"[LocalMode] ❌ {err}")
            return err

    def _chat(self, user_text: str) -> str:
        """Send message to Ollama, handle tool calls in a loop. Returns final text."""
        # Determine the response language
        response_lang = self._lang_name
        response_lang_fr = self._lang_name_fr
        if not response_lang and self._detected_lang:
            _LANG_MAP = {
                "fr": ("French", "français"), "en": ("English", "anglais"),
                "de": ("German", "allemand"), "es": ("Spanish", "espagnol"),
                "it": ("Italian", "italien"), "pt": ("Portuguese", "portugais"),
                "tr": ("Turkish", "turc"), "zh": ("Chinese", "chinois"),
                "ja": ("Japanese", "japonais"), "ko": ("Korean", "coréen"),
                "ru": ("Russian", "russe"), "ar": ("Arabic", "arabe"),
            }
            langs = _LANG_MAP.get(self._detected_lang)
            if langs:
                response_lang, response_lang_fr = langs

        # Inject language instruction in BOTH English and French
        if response_lang and response_lang != "English":
            enhanced_text = f"[Réponds en {response_lang_fr}. Respond in {response_lang}.] {user_text}"
        else:
            enhanced_text = user_text

        self.messages.append({"role": "user", "content": enhanced_text})

        max_iterations = 6   # Prevent infinite tool loops

        for iteration in range(max_iterations):
            try:
                response = self.ollama_client.chat(
                    model=self.model,
                    messages=self.messages,
                    tools=self.ollama_tools if self.ollama_tools else None,
                )
            except Exception as e:
                err_msg = f"LLM error: {e}"
                print(f"[LocalMode] ❌ {err_msg}")
                if self.messages and self.messages[-1].get("role") == "user":
                    self.messages.pop()
                return err_msg

            message = response.get("message", {})
            content = message.get("content", "")
            tool_calls = message.get("tool_calls", [])

            if tool_calls:
                # Add assistant message with tool calls
                self.messages.append(message)

                # Execute each tool
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    fn_name = fn.get("name", "")
                    fn_args = fn.get("arguments", {})

                    # Ensure args is a dict
                    if isinstance(fn_args, str):
                        try:
                            fn_args = json.loads(fn_args)
                        except json.JSONDecodeError:
                            fn_args = {}

                    result = self._execute_tool(fn_name, fn_args)
                    self.messages.append({
                        "role": "tool",
                        "content": result,
                        "name": fn_name,
                    })

                # Loop back to get the next response
                continue

            # No tool calls — final response
            if content:
                self.messages.append({"role": "assistant", "content": content})
                self._trim_history()
                return content

            return "..."

        return "I've reached the maximum tool call iterations. Let me know if you need more help."

    def _trim_history(self):
        """Keep conversation history within limits."""
        if len(self.messages) > MAX_HISTORY:
            system = self.messages[0]
            self.messages = [system] + self.messages[-(MAX_HISTORY - 1):]

    # ───────────────────────────────────────────────────────
    # TEXT-TO-SPEECH
    # ───────────────────────────────────────────────────────

    async def _speak(self, text: str):
        """Convert text to speech and play it."""
        if not text:
            return

        self.ui.set_state("SPEAKING")
        self.ui.write_log(f"Jarvis: {text[:300]}")

        try:
            import edge_tts

            # Select voice based on language setting
            voice = "en-US-GuyNeural"  # default
            lang_code = self.lang

            # Auto-detect from Whisper or text content
            if lang_code == "auto":
                lang_code = self._detected_lang or "en"
                # Fallback: detect from text content
                if lang_code == "en":
                    french_chars = sum(1 for c in text if c in "éèêëàâùûîïôöçÉÈÊËÀÂÙÛÎÏÔÖÇ")
                    if french_chars > len(text) * 0.05:
                        lang_code = "fr"

            # Get voice from config (male voice primary, female fallback)
            if lang_code in LANG_CONFIG:
                voice = LANG_CONFIG[lang_code][1]  # male voice
            else:
                # Try 2-letter prefix match
                prefix = lang_code[:2]
                if prefix in LANG_CONFIG:
                    voice = LANG_CONFIG[prefix][1]

            # Generate audio (edge-tts outputs MP3)
            communicate = edge_tts.Communicate(text, voice)
            temp_file = tempfile.mktemp(suffix=".mp3", prefix="jarvis_tts_")
            await communicate.save(temp_file)

            # Play audio
            await self._play_audio_file(temp_file)

            # Cleanup
            try:
                os.unlink(temp_file)
            except OSError:
                pass

        except ImportError:
            # No edge-tts — just print
            print(f"[LocalMode] 💬 {text[:200]}")
        except Exception as e:
            print(f"[LocalMode] ❌ TTS error: {e}")

        self.ui.set_state("LISTENING")

    async def _play_audio_file(self, filepath: str):
        """Play an audio file silently using pygame (no visible window)."""
        try:
            import pygame
            if not pygame.mixer.get_init():
                pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=2048)
            pygame.mixer.music.load(filepath)
            pygame.mixer.music.play()
            # Wait until playback finishes
            while pygame.mixer.music.get_busy():
                await asyncio.sleep(0.1)
            pygame.mixer.music.unload()
            return
        except ImportError:
            pass
        except Exception as e:
            print(f"[LocalMode] ⚠️ pygame playback failed: {e}")

        # Fallback: platform-specific players
        try:
            if sys.platform == "win32":
                # ffplay from ffmpeg — no window, auto-exit
                subprocess.run(
                    ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", filepath],
                    timeout=60,
                )
            elif sys.platform == "darwin":
                subprocess.run(["afplay", filepath], timeout=30)
            else:
                subprocess.run(["aplay", filepath], timeout=30)
        except FileNotFoundError:
            print("[LocalMode] 💬 No audio player found. Install: pip install pygame")
        except Exception as e:
            print(f"[LocalMode] ⚠️ Audio playback failed: {e}")

    # ───────────────────────────────────────────────────────
    # MAIN LOOP
    # ───────────────────────────────────────────────────────

    async def run(self):
        """Main loop: listen → transcribe → think → speak."""
        try:
            self._init_models()
        except RuntimeError as e:
            print(f"[LocalMode] ❌ Init failed: {e}")
            self.ui.write_log(f"SYS: Error — {e}")
            self.ui.set_state("THINKING")
            return

        print("[LocalMode] 🎧 Listening...")
        self.ui.write_log("SYS: Listening...")

        while self.running:
            try:
                self.ui.set_state("LISTENING")

                # 1. Record audio with VAD
                audio = await asyncio.to_thread(self.recorder.record)
                if audio is None:
                    continue

                # 2. Transcribe
                self.ui.set_state("THINKING")
                text = await asyncio.to_thread(self._transcribe, audio)
                if not text:
                    self.ui.set_state("LISTENING")
                    continue

                self.ui.write_log(f"You: {text}")

                # 3. Chat (LLM + tools)
                response = await asyncio.to_thread(self._chat, text)

                # 4. Speak response
                await self._speak(response)

            except KeyboardInterrupt:
                print("\n[LocalMode] 🔴 Shutting down...")
                self.running = False
                break
            except Exception as e:
                print(f"[LocalMode] ❌ Loop error: {e}")
                traceback.print_exc()
                await asyncio.sleep(1)

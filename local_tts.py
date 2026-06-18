"""
local_tts.py — Local text-to-speech using pyttsx3 (cross-platform).

Replaces Gemini Live API's audio output. Uses the OS's built-in speech
synthesis engine:
  - Windows: SAPI5 (Microsoft voices, e.g. "Microsoft David Desktop")
  - macOS:   NSSpeechSynthesizer (Alex, Samantha, etc.)
  - Linux:   eSpeak (if installed)

For better quality, see the optional Piper integration notes at the bottom
of this file. Piper can be added later without changing the public API.

Configuration (config/api_keys.json):
  - tts_voice    : voice name (default: "" = auto-pick first available)
  - tts_rate     : words per minute (default: 175)
  - tts_volume   : 0.0 to 1.0 (default: 1.0)
"""

import json
import sys
import threading
import logging
import re
from pathlib import Path
from typing import Optional, Callable

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("local_tts")


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

DEFAULT_RATE   = 175
DEFAULT_VOLUME = 1.0


def _load_config() -> dict:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_voice() -> str:
    return _load_config().get("tts_voice", "").strip()


def _get_rate() -> int:
    try:
        return int(_load_config().get("tts_rate", DEFAULT_RATE))
    except (ValueError, TypeError):
        return DEFAULT_RATE


def _get_volume() -> float:
    try:
        return float(_load_config().get("tts_volume", DEFAULT_VOLUME))
    except (ValueError, TypeError):
        return DEFAULT_VOLUME


class LocalTTS:
    """Local text-to-speech using pyttsx3.

    Usage:
        tts = LocalTTS()
        tts.start()
        tts.speak("Hello, sir.")
        # ... speak runs in a queue, async
        tts.stop()

    To interrupt current speech (barge-in):
        tts.stop_speaking()
    """

    def __init__(self):
        self._engine = None
        self._queue = []
        self._queue_lock = threading.Lock()
        self._speaking = False
        self._speaking_lock = threading.Lock()
        self._stop_flag = False
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self.on_speak_start: Optional[Callable[[], None]] = None
        self.on_speak_end: Optional[Callable[[], None]] = None
        # Selected voice name (after auto-pick)
        self.voice_name = ""

    def _init_engine(self):
        if self._engine is not None:
            return
        try:
            import pyttsx3
            self._engine = pyttsx3.init()
            # Apply config
            voices = self._engine.getProperty("voices")
            target_voice = _get_voice()
            selected = None
            if target_voice:
                for v in voices:
                    if target_voice.lower() in v.name.lower() or \
                       target_voice.lower() in getattr(v, "id", "").lower():
                        selected = v
                        break
            if not selected and voices:
                # Auto-pick: prefer a male voice for "JARVIS" feel if available
                male_keywords = ["david", "mark", "george", "alex", "daniel", "male"]
                for v in voices:
                    name_lower = v.name.lower()
                    if any(kw in name_lower for kw in male_keywords):
                        selected = v
                        break
                if not selected:
                    selected = voices[0]
            if selected:
                self._engine.setProperty("voice", selected.id)
                self.voice_name = selected.name
                print(f"[local_tts] 🎙️ Voice: {selected.name}")
            self._engine.setProperty("rate", _get_rate())
            self._engine.setProperty("volume", _get_volume())
        except ImportError:
            raise RuntimeError(
                "pyttsx3 is not installed. Run: pip install pyttsx3"
            )
        except Exception as e:
            raise RuntimeError(f"Could not init pyttsx3: {e}")

    def _speak_loop(self):
        """Background thread that processes the speech queue."""
        while self._running:
            text = None
            with self._queue_lock:
                if self._queue:
                    text = self._queue.pop(0)
            if text is None:
                import time
                time.sleep(0.05)
                continue
            if self._stop_flag:
                self._stop_flag = False
                continue
            try:
                with self._speaking_lock:
                    self._speaking = True
                if self.on_speak_start:
                    try:
                        self.on_speak_start()
                    except Exception:
                        pass
                # Split long text into sentences for better responsiveness
                # and to allow barge-in between sentences
                sentences = _split_sentences(text)
                for sentence in sentences:
                    if self._stop_flag:
                        break
                    if not sentence.strip():
                        continue
                    self._engine.say(sentence)
                    self._engine.runAndWait()
            except Exception as e:
                logger.error(f"TTS error: {e}")
            finally:
                with self._speaking_lock:
                    self._speaking = False
                self._stop_flag = False
                if self.on_speak_end:
                    try:
                        self.on_speak_end()
                    except Exception:
                        pass

    def start(self):
        """Start the TTS engine and queue processor."""
        if self._running:
            return
        self._init_engine()
        self._running = True
        self._thread = threading.Thread(
            target=self._speak_loop,
            daemon=True,
            name="LocalTTS-Speak",
        )
        self._thread.start()
        print("[local_tts] 🔊 TTS started")

    def stop(self):
        """Stop the TTS engine."""
        self._running = False
        self._stop_flag = True
        if self._thread:
            self._thread.join(timeout=2)
        if self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass
        print("[local_tts] 🔴 TTS stopped")

    def speak(self, text: str):
        """Queue text to be spoken. Returns immediately."""
        if not text or not text.strip():
            return
        with self._queue_lock:
            self._queue.append(text)

    def speak_now(self, text: str):
        """Clear the queue and speak this text immediately."""
        with self._queue_lock:
            self._queue.clear()
            self._queue.append(text)
        self._stop_flag = True  # interrupt current speech

    def stop_speaking(self):
        """Interrupt current speech and clear the queue (barge-in)."""
        self._stop_flag = True
        with self._queue_lock:
            self._queue.clear()
        if self._engine:
            try:
                self._engine.stop()
            except Exception:
                pass

    @property
    def is_speaking(self) -> bool:
        with self._speaking_lock:
            return self._speaking

    def list_voices(self) -> list:
        """List available voice names."""
        self._init_engine()
        voices = self._engine.getProperty("voices")
        return [{"name": v.name, "id": v.id} for v in voices]


def _split_sentences(text: str) -> list:
    """Split text into sentences for incremental TTS playback.

    Splits on ., !, ?, ; while preserving them. Keeps chunks under ~250 chars
    so pyttsx3 doesn't choke on very long inputs.
    """
    # Split on sentence boundaries, keeping the punctuation
    parts = re.split(r'(?<=[.!?;:])\s+', text)
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        # Further split very long chunks
        while len(part) > 250:
            # Find a space near the limit
            cut = part.rfind(" ", 100, 250)
            if cut == -1:
                cut = 250
            result.append(part[:cut])
            part = part[cut:].strip()
        if part:
            result.append(part)
    return result


# ─── Optional: Piper TTS support ────────────────────────────
# For higher-quality TTS, install Piper and set tts_engine="piper" in config.
# Piper voices are downloaded from https://github.com/rhasspy/piper
# Example:
#   pip install piper-tts
#   piper --download en_US-lessac-medium --model en_US-lessac-medium.onnx
# Then in config:
#   {"tts_engine": "piper", "tts_voice": "en_US-lessac-medium"}


if __name__ == "__main__":
    print("=" * 55)
    print("  MARK XXXIX-OR — Local TTS Self-Test")
    print("=" * 55)

    tts = LocalTTS()
    tts.start()

    print("\nAvailable voices:")
    for v in tts.list_voices()[:10]:
        print(f"  - {v['name']}")

    print("\nSpeaking test phrases...")
    tts.speak("Good morning, sir. JARVIS is now online and ready for your commands.")
    tts.speak("This is a test of the local text-to-speech system. How do I sound?")

    import time
    time.sleep(8)
    tts.stop()
    print("\nDone.")

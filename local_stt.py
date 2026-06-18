"""
local_stt.py — Local speech-to-text using faster-whisper.

Replaces the Gemini Live API's audio transcription. Captures audio from the
microphone, detects speech segments via simple energy-based VAD, and
transcribes them with a Whisper model running locally.

Configuration (config/api_keys.json):
  - whisper_model  : model size (default: "small")
                    Options: tiny | base | small | medium | large-v3
                    Smaller = faster, larger = more accurate.
  - whisper_language: language hint (default: "" = auto-detect)
                    Use "en", "fr", "tr", etc. to bias detection.
  - whisper_device : "cpu" | "cuda" | "auto" (default: "auto")
"""

import json
import sys
import time
import logging
import threading
from pathlib import Path
from typing import Optional, Callable
from collections import deque

import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("local_stt")


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

DEFAULT_MODEL     = "small"
DEFAULT_LANGUAGE  = ""  # auto-detect
DEFAULT_DEVICE    = "auto"

# Audio constants (match main.py for compatibility)
SAMPLE_RATE     = 16000
CHANNELS        = 1
CHUNK_SIZE      = 1024  # 64ms at 16kHz

# VAD parameters
SILENCE_THRESHOLD   = 0.012   # RMS below this = silence
SPEECH_THRESHOLD    = 0.025   # RMS above this = speech
MIN_SPEECH_FRAMES   = 8       # ~500ms minimum speech
MAX_SILENCE_FRAMES  = 30      # ~2s of silence ends a utterance
PRE_ROLL_FRAMES     = 5       # keep a bit of audio before speech detected


def _load_config() -> dict:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_model() -> str:
    return _load_config().get("whisper_model", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _get_language() -> str:
    return _load_config().get("whisper_language", DEFAULT_LANGUAGE).strip()


def _get_device() -> str:
    return _load_config().get("whisper_device", DEFAULT_DEVICE).strip() or DEFAULT_DEVICE


class LocalSTT:
    """Local speech-to-text using faster-whisper.

    Usage:
        stt = LocalSTT()
        stt.start()  # starts mic capture + VAD in a background thread
        stt.on_transcript = lambda text: print(f"User said: {text}")
        # ...
        stt.stop()
    """

    def __init__(self, model: Optional[str] = None):
        self.model_name = model or _get_model()
        self.language = _get_language()
        self.device = _get_device()
        self._whisper = None  # lazily loaded
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        self.on_transcript: Optional[Callable[[str], None]] = None
        self.on_state_change: Optional[Callable[[str], None]] = None
        # States: "idle" | "listening" | "speaking_detected" | "transcribing"
        self._state = "idle"
        # Mute control
        self.muted = False
        # Barge-in: when True, mic capture is paused (e.g. while TTS is playing)
        self._paused = False

    def _notify_state(self, state: str):
        self._state = state
        if self.on_state_change:
            try:
                self.on_state_change(state)
            except Exception:
                pass

    def _load_whisper(self):
        if self._whisper is not None:
            return
        print(f"[local_stt] Loading Whisper model '{self.model_name}'...")
        try:
            from faster_whisper import WhisperModel
            # device_index=None → auto-select
            self._whisper = WhisperModel(
                self.model_name,
                device=self.device if self.device != "auto" else "auto",
                compute_type="int8",  # fast on CPU
            )
            print(f"[local_stt] ✓ Whisper model '{self.model_name}' loaded")
        except ImportError:
            raise RuntimeError(
                "faster-whisper is not installed. Run: pip install faster-whisper"
            )
        except Exception as e:
            raise RuntimeError(f"Could not load Whisper model: {e}")

    def _capture_loop(self):
        """Background thread: capture mic audio, VAD, transcribe."""
        import sounddevice as sd

        # Buffer for current speech segment
        speech_buffer = deque()
        silence_count = 0
        speech_count = 0
        pre_roll = deque(maxlen=PRE_ROLL_FRAMES)

        def callback(indata, frames, time_info, status):
            if self._paused or self.muted:
                return
            # Compute RMS for VAD
            audio = indata[:, 0] if indata.ndim > 1 else indata
            rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))

            if rms > SPEECH_THRESHOLD:
                # Speech detected
                if speech_count == 0:
                    # Just started speaking — add pre-roll for context
                    for frame in pre_roll:
                        speech_buffer.append(frame)
                speech_buffer.append(audio.copy())
                speech_count += 1
                silence_count = 0
                if speech_count == 1:
                    self._notify_state("speaking_detected")
            elif rms < SILENCE_THRESHOLD and speech_count > 0:
                # In silence after speech
                silence_count += 1
                # Keep the silence audio too (helps Whisper with word endings)
                speech_buffer.append(audio.copy())
                if silence_count >= MAX_SILENCE_FRAMES and speech_count >= MIN_SPEECH_FRAMES:
                    # End of utterance — transcribe
                    self._notify_state("transcribing")
                    audio_data = np.concatenate(list(speech_buffer))
                    speech_buffer.clear()
                    speech_count = 0
                    silence_count = 0
                    pre_roll.clear()
                    # Transcribe in this thread (blocks capture briefly, but ok)
                    text = self._transcribe(audio_data)
                    if text and self.on_transcript:
                        try:
                            self.on_transcript(text)
                        except Exception as e:
                            logger.error(f"on_transcript callback failed: {e}")
                    self._notify_state("listening")
            else:
                # Background noise — track in pre-roll
                pre_roll.append(audio.copy())
                if speech_count > 0:
                    # Minor pause, keep counting
                    speech_buffer.append(audio.copy())
                    silence_count += 1
                    if silence_count >= MAX_SILENCE_FRAMES and speech_count >= MIN_SPEECH_FRAMES:
                        self._notify_state("transcribing")
                        audio_data = np.concatenate(list(speech_buffer))
                        speech_buffer.clear()
                        speech_count = 0
                        silence_count = 0
                        pre_roll.clear()
                        text = self._transcribe(audio_data)
                        if text and self.on_transcript:
                            try:
                                self.on_transcript(text)
                            except Exception as e:
                                logger.error(f"on_transcript callback failed: {e}")
                        self._notify_state("listening")

        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                self._notify_state("listening")
                while self._running:
                    time.sleep(0.1)
        except Exception as e:
            logger.error(f"Mic capture error: {e}")
            self._notify_state("idle")

    def _transcribe(self, audio: np.ndarray) -> str:
        """Transcribe a numpy audio array (float32, 16kHz, mono)."""
        try:
            self._load_whisper()
            # faster-whisper wants float32 in [-1, 1]
            segments, info = self._whisper.transcribe(
                audio,
                language=self.language if self.language else None,
                beam_size=1,           # fast
                vad_filter=True,       # built-in VAD for trimming
                without_timestamps=True,
            )
            text = " ".join(seg.text for seg in segments).strip()
            return text
        except Exception as e:
            logger.error(f"Transcription failed: {e}")
            return ""

    def transcribe_file(self, file_path: str) -> str:
        """Transcribe an audio file (any format supported by ffmpeg)."""
        self._load_whisper()
        try:
            segments, info = self._whisper.transcribe(
                file_path,
                language=self.language if self.language else None,
                beam_size=5,
                vad_filter=True,
            )
            return " ".join(seg.text for seg in segments).strip()
        except Exception as e:
            logger.error(f"File transcription failed: {e}")
            return ""

    def start(self):
        """Start mic capture + VAD in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._capture_loop,
            daemon=True,
            name="LocalSTT-Capture",
        )
        self._thread.start()
        print("[local_stt] 🎤 Mic capture started")

    def stop(self):
        """Stop mic capture."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        self._notify_state("idle")
        print("[local_stt] 🔴 Mic capture stopped")

    def pause(self):
        """Pause transcription (e.g. while TTS is playing)."""
        self._paused = True

    def resume(self):
        """Resume transcription."""
        self._paused = False

    @property
    def state(self) -> str:
        return self._state


if __name__ == "__main__":
    print("=" * 55)
    print("  MARK XXXIX-OR — Local STT Self-Test")
    print("=" * 55)
    print("\nSpeak into your microphone. Press Ctrl+C to stop.\n")

    stt = LocalSTT()
    stt.on_transcript = lambda text: print(f"\n  👤 You said: {text}\n")
    stt.on_state_change = lambda s: print(f"  [state: {s}]")
    stt.start()

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\n\nStopping...")
        stt.stop()

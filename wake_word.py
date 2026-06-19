"""
wake_word.py — Local wake word detector for "Jarvis".

Listens to the microphone continuously and fires a callback when the word
"Jarvis" is detected. 100% local — no API calls, no quota usage.

Two detection strategies (tried in order):
  1. Vosk offline (if installed) — accurate, multilingual, ~50MB model
  2. Simple energy + phonetic heuristic — no extra deps, less accurate

When the wake word is detected, the callback is called and the detector
pauses for `cooldown` seconds to avoid double-triggering.

Usage:
    from wake_word import WakeWordDetector
    detector = WakeWordDetector(on_detected=lambda: print("Jarvis!"))
    detector.start()
    # ... later ...
    detector.stop()
"""

import threading
import time
import logging
import sys
from pathlib import Path
from typing import Callable, Optional

import numpy as np

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("wake_word")


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR = _get_base_dir()

# Audio constants
SAMPLE_RATE = 16000
CHANNELS    = 1
CHUNK_SIZE  = 1024  # 64ms at 16kHz

# VAD parameters (same as local_stt.py for consistency)
SILENCE_THRESHOLD   = 0.012
SPEECH_THRESHOLD    = 0.025
MIN_SPEECH_FRAMES   = 3    # ~200ms minimum for "Jarvis"
MAX_SILENCE_FRAMES  = 20   # ~1.3s of silence ends an utterance

# Wake word detection
COOLDOWN_SEC  = 3.0  # seconds to wait after a detection before re-arming
WAKE_WORDS    = ["jarvis", "jarvi", "javis", "charvis", "jervis", "jarv"]
              # include common misrecognitions


class WakeWordDetector:
    """Local wake word detector that listens for 'Jarvis'.

    Uses Vosk for offline recognition if available, otherwise falls back
    to a simple speech_recognition library, or energy-based VAD only
    (least accurate — triggers on any speech).

    The detector runs in a daemon thread and calls `on_detected` when the
    wake word is heard. Between detections, a cooldown prevents
    double-triggering.
    """

    def __init__(self, on_detected: Callable[[], None]):
        self.on_detected = on_detected
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_detection = 0.0
        self._engine = None
        self._engine_type = None

    def _init_engine(self):
        """Try to initialise the best available recognition engine."""
        # Strategy 1: Vosk (offline, accurate, multilingual)
        try:
            import json as _json
            from vosk import Model, KaldiRecognizer
            import sounddevice as sd

            # Try to find a Vosk model
            model_paths = [
                BASE_DIR / "models" / "vosk-model-small-en-us-0.15",
                BASE_DIR / "models" / "vosk-model-small-fr-0.22",
                BASE_DIR / "models" / "vosk-model-en-us-0.22",
                Path.home() / "vosk-model",
                Path("/usr/share/vosk-model"),
            ]

            # Also check if vosk can download automatically
            model = None
            for p in model_paths:
                if p.exists() and (p / "conf" / "mfcc.conf").exists():
                    model = Model(str(p))
                    break

            if model is None:
                # Vosk is installed but no model — try to use the small
                # English model that pip install vosk sometimes bundles
                try:
                    import vosk
                    vosk_path = Path(vosk.__file__).parent / "models"
                    for p in vosk_path.iterdir() if vosk_path.exists() else []:
                        if (p / "conf" / "mfcc.conf").exists():
                            model = Model(str(p))
                            break
                except Exception:
                    pass

            if model is not None:
                self._engine = ("vosk", model)
                self._engine_type = "vosk"
                print("[wake_word] ✅ Using Vosk offline engine")
                return
            else:
                print("[wake_word] Vosk installed but no model found — trying other engines")
        except ImportError:
            pass
        except Exception as e:
            print(f"[wake_word] Vosk init failed: {e}")

        # Strategy 2: speech_recognition with PocketSphinx (offline)
        try:
            import speech_recognition as sr
            self._engine = ("sr", sr.Recognizer())
            self._engine_type = "sr"
            # Configure for continuous listening with PocketSphinx
            self._engine[1].energy_threshold = 300
            self._engine[1].dynamic_energy_threshold = True
            print("[wake_word] ✅ Using speech_recognition + PocketSphinx")
            return
        except ImportError:
            pass
        except Exception as e:
            print(f"[wake_word] speech_recognition init failed: {e}")

        # Strategy 3: Energy-based VAD only (triggers on ANY speech)
        # This is a fallback — it can't actually detect "Jarvis" but
        # it will trigger on speech and let the caller decide.
        self._engine = ("energy", None)
        self._engine_type = "energy"
        print("[wake_word] ⚠️ No recognition engine — using energy-based VAD (any speech triggers)")

    def _vosk_loop(self):
        """Continuous listening loop with Vosk."""
        import json as _json
        import sounddevice as sd
        from vosk import KaldiRecognizer

        model = self._engine[1]
        rec = KaldiRecognizer(model, SAMPLE_RATE)
        rec.SetWords(True)

        def callback(indata, frames, time_info, status):
            if not self._running:
                return
            audio_bytes = (indata * 32767).astype(np.int16).tobytes()
            if rec.AcceptWaveform(audio_bytes):
                result = _json.loads(rec.Result())
                text = result.get("text", "").lower().strip()
                if text:
                    self._check_wake_word(text)

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=CHUNK_SIZE,
            callback=callback,
        ):
            while self._running:
                time.sleep(0.05)

    def _sr_loop(self):
        """Continuous listening loop with speech_recognition."""
        import speech_recognition as sr

        recognizer = self._engine[1]
        mic = sr.Microphone(sample_rate=SAMPLE_RATE)

        # Adjust for ambient noise
        with mic as source:
            recognizer.adjust_for_ambient_noise(source, duration=1)

        while self._running:
            try:
                with mic as source:
                    audio = recognizer.listen(source, timeout=1, phrase_time_limit=5)

                # Use PocketSphinx for offline recognition
                try:
                    text = recognizer.recognize_sphinx(audio).lower().strip()
                    if text:
                        self._check_wake_word(text)
                except sr.UnknownValueError:
                    pass  # couldn't understand — ignore
                except Exception:
                    pass
            except sr.WaitTimeoutError:
                continue
            except Exception as e:
                if self._running:
                    logger.error(f"SR loop error: {e}")
                time.sleep(0.5)

    def _energy_loop(self):
        """Energy-based VAD fallback — triggers on any speech."""
        import sounddevice as sd

        speech_count = 0
        silence_count = 0

        def callback(indata, frames, time_info, status):
            nonlocal speech_count, silence_count
            if not self._running:
                return
            audio = indata[:, 0] if indata.ndim > 1 else indata
            rms = float(np.sqrt(np.mean(audio.astype(np.float32) ** 2)))

            if rms > SPEECH_THRESHOLD:
                speech_count += 1
                silence_count = 0
            elif rms < SILENCE_THRESHOLD and speech_count > 0:
                silence_count += 1
                if silence_count >= MAX_SILENCE_FRAMES and speech_count >= MIN_SPEECH_FRAMES:
                    # Speech detected — trigger wake word
                    self._trigger()
                    speech_count = 0
                    silence_count = 0
            else:
                if silence_count >= MAX_SILENCE_FRAMES:
                    speech_count = 0

        with sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=CHANNELS,
            dtype="float32",
            blocksize=CHUNK_SIZE,
            callback=callback,
        ):
            while self._running:
                time.sleep(0.05)

    def _check_wake_word(self, text: str):
        """Check if the recognized text contains the wake word."""
        now = time.time()
        if now - self._last_detection < COOLDOWN_SEC:
            return  # cooldown not elapsed

        text_lower = text.lower()
        for word in WAKE_WORDS:
            if word in text_lower:
                print(f"[wake_word] 🎯 Detected '{word}' in: '{text[:60]}'")
                self._trigger()
                return

    def _trigger(self):
        """Fire the callback and start cooldown."""
        now = time.time()
        if now - self._last_detection < COOLDOWN_SEC:
            return
        self._last_detection = now
        print("[wake_word] ✅ Wake word detected! Unmuting Jarvis.")
        try:
            self.on_detected()
        except Exception as e:
            logger.error(f"on_detected callback error: {e}")

    def start(self):
        """Start the wake word detector in a background thread."""
        if self._running:
            return
        self._running = True
        self._init_engine()

        target = {
            "vosk":   self._vosk_loop,
            "sr":     self._sr_loop,
            "energy": self._energy_loop,
        }.get(self._engine_type, self._energy_loop)

        self._thread = threading.Thread(
            target=target,
            daemon=True,
            name="WakeWordDetector",
        )
        self._thread.start()
        print(f"[wake_word] 🎤 Started (engine={self._engine_type})")

    def stop(self):
        """Stop the wake word detector."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2)
        print("[wake_word] 🔴 Stopped")

"""
screen_processor.py — Local vision module using Ollama VLM (llava).

Replaces the Gemini Live API vision session. Now much simpler:
  1. Capture screenshot (mss) or webcam frame (OpenCV)
  2. Send to local VLM (llava) via local_llm.client.vision_from_bytes()
  3. TTS the response via local_tts

No more WebSocket sessions, no more Live API, no more 1008/1011 errors.
"""

import base64
import io
import json
import sys
import threading
import time
from pathlib import Path

from config import is_windows, is_mac, is_linux


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

IMG_MAX_W = 640
IMG_MAX_H = 360
JPEG_Q    = 55

SYSTEM_PROMPT = (
    "You are JARVIS from Iron Man movies. "
    "Analyze images with technical precision and intelligence. "
    "Help the user in a way they can understand — don't be overly complex. "
    "Be concise, smart, and helpful like Tony Stark's AI assistant. "
    "Respond in maximum 2 short sentences. Speed is priority. "
    "Address the user as 'sir' for a tone of respect. "
    "Ask if the user needs any further help with their problem."
)


def _get_api_key() -> str:
    """Legacy compat — returns empty string in local mode."""
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("gemini_api_key", "")
    except Exception:
        return ""


def _get_camera_index() -> int:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        if "camera_index" in cfg:
            return int(cfg["camera_index"])
    except Exception:
        pass
    return 0


# ─── Image capture ─────────────────────────────────────────

def _capture_screenshot() -> bytes:
    """Capture the primary display as compressed JPEG bytes."""
    try:
        import mss
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(f"mss or Pillow not installed: {e}")

    with mss.mss() as sct:
        monitor = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        raw = sct.grab(monitor)
        # Convert BGRA → RGB
        img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
        # Downscale for speed
        img.thumbnail((IMG_MAX_W, IMG_MAX_H))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=JPEG_Q)
        return buf.getvalue()


def _capture_camera() -> bytes:
    """Capture a single frame from the webcam as JPEG bytes."""
    try:
        import cv2
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(f"opencv-python or Pillow not installed: {e}")

    idx = _get_camera_index()
    cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW if is_windows() else 0)
    if not cap.isOpened():
        # Try other indices
        for try_idx in range(6):
            cap = cv2.VideoCapture(try_idx, cv2.CAP_DSHOW if is_windows() else 0)
            if cap.isOpened():
                idx = try_idx
                # Save for next time
                try:
                    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                    cfg["camera_index"] = idx
                    with open(API_CONFIG_PATH, "w", encoding="utf-8") as f:
                        json.dump(cfg, f, indent=2)
                except Exception:
                    pass
                break
        else:
            raise RuntimeError("Could not open any webcam (tried indices 0-5)")

    # Warm up
    for _ in range(10):
        cap.read()
        time.sleep(0.02)

    ret, frame = cap.read()
    cap.release()
    if not ret or frame is None:
        raise RuntimeError("Webcam read failed")

    # BGR → RGB
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    img = Image.fromarray(frame_rgb)
    img.thumbnail((IMG_MAX_W, IMG_MAX_H))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_Q)
    return buf.getvalue()


# ─── Vision analysis ───────────────────────────────────────

def _analyze_image(image_bytes: bytes, user_text: str) -> str:
    """Send image + question to local VLM (llava) and get response."""
    from local_llm import client as llm_client
    return llm_client.vision_from_bytes(
        prompt=user_text,
        image_bytes=image_bytes,
        system=SYSTEM_PROMPT,
        temperature=0.2,
        max_tokens=300,
    )


# ─── Public entry point (called from main.py) ──────────────

def screen_process(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> bool:
    """Capture screen/camera, analyze with local VLM, TTS the response.

    Args:
        parameters: {text: str, angle: 'screen' | 'camera'}
        player:     JarvisUI instance (for logging)
    Returns:
        True on success
    """
    params = parameters or {}
    user_text = (params.get("text") or params.get("user_text") or "").strip()
    angle = (params.get("angle") or "screen").strip().lower()

    if not user_text:
        if player:
            player.write_log("[vision] No question provided")
        return False

    print(f"[ScreenProcess] 📷 Capturing {angle}...")
    if player:
        player.write_log(f"[vision] analyzing {angle}...")

    try:
        if angle == "camera":
            image_bytes = _capture_camera()
        else:
            image_bytes = _capture_screenshot()
    except Exception as e:
        print(f"[ScreenProcess] ❌ Capture failed: {e}")
        if player:
            player.write_log(f"[vision] capture failed: {e}")
        # Try to TTS the error
        try:
            from local_tts import LocalTTS
            tts = LocalTTS()
            tts.start()
            tts.speak(f"Sir, I could not capture the {angle}. {str(e)[:80]}")
            time.sleep(3)
            tts.stop()
        except Exception:
            pass
        return False

    print(f"[ScreenProcess] 🧠 Analyzing with local VLM...")
    try:
        result = _analyze_image(image_bytes, user_text)
    except Exception as e:
        print(f"[ScreenProcess] ❌ VLM analysis failed: {e}")
        if player:
            player.write_log(f"[vision] analysis failed: {e}")
        return False

    if not result or result.startswith("[local_llm error"):
        print(f"[ScreenProcess] ⚠️ VLM returned error: {result[:200]}")
        if player:
            player.write_log(f"[vision] {result[:100]}")
        return False

    print(f"[ScreenProcess] ✅ {result[:200]}")
    if player:
        player.write_log(f"Jarvis: {result}")

    # TTS the response
    try:
        from local_tts import LocalTTS
        tts = LocalTTS()
        tts.start()
        tts.speak(result)
        # Wait up to 30s for speech to finish
        deadline = time.time() + 30
        while tts.is_speaking and time.time() < deadline:
            time.sleep(0.1)
        time.sleep(0.5)
        tts.stop()
    except Exception as e:
        print(f"[ScreenProcess] ⚠️ TTS failed: {e}")

    return True


# ─── Background pre-warm (optional) ────────────────────────

_warmup_done = False
_warmup_lock = threading.Lock()


def warmup_session(player=None):
    """Pre-load the VLM model so the first real call is faster."""
    global _warmup_done
    with _warmup_lock:
        if _warmup_done:
            return
        _warmup_done = True
    try:
        from local_llm import _ensure_ollama_running, _ensure_model_pulled, _get_vision_model
        if _ensure_ollama_running():
            _ensure_model_pulled(_get_vision_model())
            print("[ScreenProcess] ✅ VLM pre-warmed")
        else:
            print("[ScreenProcess] ⚠️ Ollama not running — vision will be slow on first use")
    except Exception as e:
        print(f"[ScreenProcess] ⚠️ Warmup failed: {e}")


if __name__ == "__main__":
    print("=" * 55)
    print("  MARK XXXIX-OR — Local Vision Self-Test")
    print("=" * 55)
    print("\nCapturing screen and asking 'What do you see?'\n")
    screen_process(parameters={"text": "What do you see on the screen?", "angle": "screen"})

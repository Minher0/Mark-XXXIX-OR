"""
local_llm.py — Local LLM client via Ollama.

Replaces the Gemini API for all LLM operations:
  - chat()                 : single-turn text generation
  - chat_with_history()    : multi-turn with conversation context
  - chat_with_tools()      : tool-calling (OpenAI-compatible format)
  - vision()               : image + prompt analysis (using a VLM model)
  - stream_chat()          : streaming text generation (for TTS chunking)

Configuration (config/api_keys.json):
  - local_model      : text model name (default: "llama3.2:3b")
  - local_vision_model: vision model name (default: "llava:7b")
  - ollama_host      : Ollama server URL (default: "http://localhost:11434")

If Ollama is not running, the client will attempt to start it via subprocess
on Windows/macOS/Linux. If the model is not pulled, it will be pulled
automatically on first use (large download).
"""

import json
import os
import subprocess
import sys
import time
import logging
from pathlib import Path
from typing import Optional, Generator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("local_llm")


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

DEFAULT_HOST          = "http://localhost:11434"
DEFAULT_TEXT_MODEL    = "llama3.2:3b"
DEFAULT_VISION_MODEL  = "llava:7b"


def _load_config() -> dict:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_host() -> str:
    cfg = _load_config()
    return cfg.get("ollama_host", DEFAULT_HOST).strip() or DEFAULT_HOST


def _get_text_model() -> str:
    cfg = _load_config()
    return cfg.get("local_model", DEFAULT_TEXT_MODEL).strip() or DEFAULT_TEXT_MODEL


def _get_vision_model() -> str:
    cfg = _load_config()
    return cfg.get("local_vision_model", DEFAULT_VISION_MODEL).strip() or DEFAULT_VISION_MODEL


def _ensure_ollama_running() -> bool:
    """Check if Ollama is running; if not, try to start it.

    Returns True if Ollama is reachable (was running or just started).
    """
    try:
        import requests
        r = requests.get(f"{_get_host()}/api/tags", timeout=2)
        if r.status_code == 200:
            return True
    except Exception:
        pass

    # Try to start Ollama as a background service
    print("[local_llm] Ollama not running — attempting to start it...")
    try:
        if sys.platform == "win32":
            # On Windows, `ollama serve` runs in foreground. Use `start` to
            # detach it. Also try the installed path.
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        # Wait for it to come up
        for _ in range(15):
            time.sleep(1)
            try:
                import requests
                r = requests.get(f"{_get_host()}/api/tags", timeout=2)
                if r.status_code == 200:
                    print("[local_llm] ✓ Ollama is now running")
                    return True
            except Exception:
                continue
        print("[local_llm] ✗ Ollama did not start within 15s")
        return False
    except FileNotFoundError:
        print("[local_llm] ✗ 'ollama' command not found. Install from https://ollama.com/download")
        return False
    except Exception as e:
        print(f"[local_llm] ✗ Could not start Ollama: {e}")
        return False


def _ensure_model_pulled(model: str) -> bool:
    """Pull a model if it's not already available locally."""
    try:
        import requests
        r = requests.get(f"{_get_host()}/api/tags", timeout=5)
        if r.status_code == 200:
            models = r.json().get("models", [])
            for m in models:
                if m.get("name", "").lower().startswith(model.lower()) or \
                   m.get("model", "").lower().startswith(model.lower()):
                    return True
    except Exception:
        pass

    print(f"[local_llm] Pulling model '{model}' (this may take a while)...")
    try:
        # Use the CLI to pull (streaming download progress)
        result = subprocess.run(
            ["ollama", "pull", model],
            capture_output=False,
            timeout=3600,  # 1 hour max
        )
        if result.returncode == 0:
            print(f"[local_llm] ✓ Model '{model}' pulled successfully")
            return True
        print(f"[local_llm] ✗ Pull failed with code {result.returncode}")
        return False
    except FileNotFoundError:
        print("[local_llm] ✗ 'ollama' command not found")
        return False
    except Exception as e:
        print(f"[local_llm] ✗ Pull failed: {e}")
        return False


def _convert_gemini_tools_to_ollama(gemini_tools: list) -> list:
    """Convert Gemini-style tool declarations to Ollama/OpenAI format.

    Gemini format:
        [{"name": "open_app", "description": "...", "parameters": {...}}]
    Ollama format (OpenAI-compatible):
        [{"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}]
    """
    converted = []
    for tool in gemini_tools:
        if "function" in tool:
            # Already in OpenAI format
            converted.append(tool)
            continue
        params = dict(tool.get("parameters", {}) or {})
        # Normalise type names (Gemini uses uppercase, OpenAI uses lowercase)
        if "type" in params and isinstance(params["type"], str):
            params["type"] = params["type"].lower()
        # Recursively convert nested property types
        if "properties" in params:
            for prop_name, prop_schema in params["properties"].items():
                if isinstance(prop_schema, dict) and "type" in prop_schema:
                    if isinstance(prop_schema["type"], str):
                        prop_schema["type"] = prop_schema["type"].lower()
        converted.append({
            "type": "function",
            "function": {
                "name": tool.get("name", ""),
                "description": tool.get("description", ""),
                "parameters": params,
            },
        })
    return converted


class LocalLLM:

    def __init__(self, model: Optional[str] = None):
        self.model = model or _get_text_model()
        self.host = _get_host()
        self._initialised = False

    def _ensure_ready(self) -> bool:
        """Lazily ensure Ollama is running and the model is pulled."""
        if self._initialised:
            return True
        if not _ensure_ollama_running():
            return False
        if not _ensure_model_pulled(self.model):
            return False
        self._initialised = True
        return True

    def chat(
        self,
        prompt: str,
        system: str = "You are JARVIS, an AI assistant. Be concise and helpful.",
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Single-turn chat. Returns the assistant's text response."""
        if not self._ensure_ready():
            return f"[local_llm error: Ollama not available]"

        try:
            import ollama
            client = ollama.Client(host=self.host)
            response = client.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt},
                ],
                options={"temperature": temperature, "num_predict": max_tokens},
            )
            return response.get("message", {}).get("content", "").strip()
        except Exception as e:
            logger.error(f"chat() failed: {e}")
            return f"[local_llm error: {e}]"

    def chat_with_history(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> str:
        """Multi-turn chat. `messages` is a list of {role, content} dicts."""
        if not self._ensure_ready():
            return "[local_llm error: Ollama not available]"

        try:
            import ollama
            client = ollama.Client(host=self.host)
            response = client.chat(
                model=self.model,
                messages=messages,
                options={"temperature": temperature, "num_predict": max_tokens},
            )
            return response.get("message", {}).get("content", "").strip()
        except Exception as e:
            logger.error(f"chat_with_history() failed: {e}")
            return f"[local_llm error: {e}]"

    def chat_with_tools(
        self,
        messages: list,
        tools: list,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> dict:
        """Chat with tool-calling support.

        Args:
            messages: list of {role, content} dicts (system/user/assistant/tool)
            tools:    Gemini-style OR OpenAI-style tool declarations

        Returns:
            {
                "content":      str  (assistant's text response, may be empty),
                "tool_calls":   list (each {"name": str, "arguments": dict}),
                "finish_reason": str ("stop" | "tool_calls" | "length"),
            }
        """
        if not self._ensure_ready():
            return {
                "content": "[local_llm error: Ollama not available]",
                "tool_calls": [],
                "finish_reason": "stop",
            }

        ollama_tools = _convert_gemini_tools_to_ollama(tools)

        try:
            import ollama
            client = ollama.Client(host=self.host)
            response = client.chat(
                model=self.model,
                messages=messages,
                tools=ollama_tools if ollama_tools else None,
                options={"temperature": temperature, "num_predict": max_tokens},
            )
            msg = response.get("message", {})
            content = msg.get("content", "") or ""
            tool_calls_raw = msg.get("tool_calls", []) or []

            tool_calls = []
            for tc in tool_calls_raw:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                # Ollama sometimes returns arguments as a JSON string
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        args = {"_raw": args}
                tool_calls.append({"name": name, "arguments": args})

            finish = "tool_calls" if tool_calls else "stop"
            return {
                "content": content.strip(),
                "tool_calls": tool_calls,
                "finish_reason": finish,
            }
        except Exception as e:
            logger.error(f"chat_with_tools() failed: {e}")
            return {
                "content": f"[local_llm error: {e}]",
                "tool_calls": [],
                "finish_reason": "stop",
            }

    def stream_chat(
        self,
        messages: list,
        tools: Optional[list] = None,
        temperature: float = 0.7,
        max_tokens: int = 2048,
    ) -> Generator[dict, None, None]:
        """Stream chat tokens. Yields dicts:
            {"type": "text", "content": "..."} for text deltas
            {"type": "tool_call", "name": "...", "arguments": {...}} for tool calls
            {"type": "done", "finish_reason": "stop"|"tool_calls"|"length"} at end
        """
        if not self._ensure_ready():
            yield {"type": "text", "content": "[local_llm error: Ollama not available]"}
            yield {"type": "done", "finish_reason": "stop"}
            return

        ollama_tools = _convert_gemini_tools_to_ollama(tools) if tools else None

        try:
            import ollama
            client = ollama.Client(host=self.host)
            stream = client.chat(
                model=self.model,
                messages=messages,
                tools=ollama_tools,
                stream=True,
                options={"temperature": temperature, "num_predict": max_tokens},
            )

            pending_tool_calls = []
            for chunk in stream:
                msg = chunk.get("message", {})
                content = msg.get("content", "")
                if content:
                    yield {"type": "text", "content": content}

                tcs = msg.get("tool_calls", [])
                for tc in tcs:
                    fn = tc.get("function", {})
                    name = fn.get("name", "")
                    args = fn.get("arguments", {})
                    if isinstance(args, str):
                        try:
                            args = json.loads(args)
                        except json.JSONDecodeError:
                            args = {"_raw": args}
                    pending_tool_calls.append({"name": name, "arguments": args})

                if chunk.get("done"):
                    finish = "tool_calls" if pending_tool_calls else "stop"
                    for tc in pending_tool_calls:
                        yield {"type": "tool_call", "name": tc["name"], "arguments": tc["arguments"]}
                    yield {"type": "done", "finish_reason": finish}
                    return

            # Stream ended without explicit done flag
            for tc in pending_tool_calls:
                yield {"type": "tool_call", "name": tc["name"], "arguments": tc["arguments"]}
            yield {"type": "done", "finish_reason": "tool_calls" if pending_tool_calls else "stop"}

        except Exception as e:
            logger.error(f"stream_chat() failed: {e}")
            yield {"type": "text", "content": f"[local_llm error: {e}]"}
            yield {"type": "done", "finish_reason": "stop"}

    def vision(
        self,
        prompt: str,
        image_path: str,
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        """Analyze an image with a text prompt using a vision-capable model."""
        vision_model = _get_vision_model()
        if not _ensure_ollama_running():
            return "[local_llm error: Ollama not available]"
        if not _ensure_model_pulled(vision_model):
            return f"[local_llm error: vision model '{vision_model}' not available]"

        try:
            import ollama
            client = ollama.Client(host=self.host)
            response = client.chat(
                model=vision_model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": prompt, "images": [image_path]},
                ],
                options={"temperature": temperature, "num_predict": max_tokens},
            )
            return response.get("message", {}).get("content", "").strip()
        except Exception as e:
            logger.error(f"vision() failed: {e}")
            return f"[local_llm error: {e}]"

    def vision_from_bytes(
        self,
        prompt: str,
        image_bytes: bytes,
        system: str = "Analyze the image and describe what you see clearly and concisely.",
        temperature: float = 0.2,
        max_tokens: int = 1024,
    ) -> str:
        """Analyze image bytes (e.g. screenshot) with a text prompt."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(image_bytes)
            tmp_path = f.name
        try:
            return self.vision(prompt, tmp_path, system, temperature, max_tokens)
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


# Module-level singleton (mirrors or_client.py pattern)
client = LocalLLM()


def is_available() -> bool:
    """Quick check: is Ollama running and reachable?"""
    try:
        import requests
        r = requests.get(f"{_get_host()}/api/tags", timeout=2)
        return r.status_code == 200
    except Exception:
        return False


def get_status() -> dict:
    """Return diagnostic info about the local LLM setup."""
    cfg = _load_config()
    return {
        "host": _get_host(),
        "text_model": _get_text_model(),
        "vision_model": _get_vision_model(),
        "running": is_available(),
    }


if __name__ == "__main__":
    print("=" * 55)
    print("  MARK XXXIX-OR — Local LLM (Ollama) Self-Test")
    print("=" * 55)

    status = get_status()
    print(f"\n  Host         : {status['host']}")
    print(f"  Text model   : {status['text_model']}")
    print(f"  Vision model : {status['vision_model']}")
    print(f"  Running      : {status['running']}")

    print("\n[TEST 1] Basic chat...")
    reply = client.chat("Introduce yourself in one sentence.")
    print(f"  Response: {reply[:200]}")

    print("\n[TEST 2] Tool calling...")
    tools = [{
        "name": "get_weather",
        "description": "Get the weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    }]
    result = client.chat_with_tools(
        messages=[{"role": "user", "content": "What's the weather in Paris?"}],
        tools=tools,
    )
    print(f"  Content    : {result['content'][:200]}")
    print(f"  Tool calls : {result['tool_calls']}")

    print("\n" + "=" * 55)
    print("  All tests complete.")
    print("=" * 55)

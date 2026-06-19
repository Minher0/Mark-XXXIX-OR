"""
multi_key_llm.py — Multi-key Gemini LLM with automatic key rotation.

When Google's free-tier rate limit (429) is hit on one API key, this LLM
automatically rotates to the next key. With N keys from N different Google
Cloud projects, you get N× the daily quota.

This class SUBCLASSES ChatGoogleGenerativeAI so that browser-use (and any
other LangChain consumer that does isinstance() checks) accepts it as a
valid Gemini LLM. We only override the internal `_generate` and `_agenerate`
methods to intercept 429 errors and rotate keys before retrying.

Usage:
    from multi_key_llm import MultiKeyGeminiLLM
    llm = MultiKeyGeminiLLM(
        api_keys=["key1", "key2", "key3"],
        model="gemini-2.0-flash",
        temperature=0.1,
    )
    # Use as a drop-in replacement for ChatGoogleGenerativeAI
    # browser-use can use it directly: Agent(llm=llm, ...)

Config (config/api_keys.json):
    {
        "gemini_api_keys": ["key1", "key2", "key3"],  // plural — pooled
        "gemini_api_key": "key1"                       // singular — backward compat
    }
    If gemini_api_keys is present and non-empty, it takes precedence.
"""

import time
import logging

logger = logging.getLogger("multi_key_llm")


def _make_multi_key_class():
    """Build the MultiKeyGeminiLLM class by subclassing ChatGoogleGenerativeAI.

    We do this in a factory function so that the import of
    ChatGoogleGenerativeAI happens lazily (only when the class is actually
    needed). This keeps the module importable even if
    langchain-google-genai is not installed.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    class MultiKeyGeminiLLM(ChatGoogleGenerativeAI):
        """ChatGoogleGenerativeAI subclass that rotates API keys on 429.

        Inherits everything from ChatGoogleGenerativeAI so isinstance()
        checks pass. Only overrides _generate and _agenerate to intercept
        429 errors and rotate to the next key before retrying.

        Key rotation strategy:
          1. On 429, mark the current key as "cooling down" for 60 seconds
          2. Find the next key that is NOT cooling down
          3. Swap self.google_api_key to that key
          4. Retry the call
          5. If ALL keys are cooling down, use the one with the earliest
             cooldown expiry
        """

        # Explicit provider attribute for browser-use compatibility.
        # Some browser-use versions check llm.provider to know how to
        # format messages. ChatGoogleGenerativeAI may or may not have it
        # depending on the langchain-google-genai version, so we set it
        # explicitly here to be safe.
        provider: str = "google"

        def __init__(self, api_keys, model, **kwargs):
            if not api_keys:
                raise ValueError("MultiKeyGeminiLLM requires at least one API key")

            self._keys = list(api_keys)
            self._idx = 0
            self._cooldowns = {}  # key_index -> cooldown_until_timestamp

            # Initialize the parent class with the first key
            super().__init__(
                model=model,
                google_api_key=self._keys[0],
                **kwargs,
            )
            print(f"[multi_key_llm] 🔑 Initialized with {len(self._keys)} API key(s)")

        def _is_rate_limit_error(self, error) -> bool:
            """Check if an error is a 429 rate limit error."""
            err_str = str(error)
            return ("429" in err_str or
                    "RESOURCE_EXHAUSTED" in err_str or
                    "quota" in err_str.lower())

        def _rotate_key(self) -> bool:
            """Try to rotate to the next available key.

            Returns True if rotation succeeded, False if all keys are cooling
            down (in which case we use the one with the earliest cooldown
            expiry).
            """
            now = time.time()
            # Mark current key as cooling down for 60s
            self._cooldowns[self._idx] = now + 60
            logger.warning(
                f"[multi_key_llm] ⏳ Key #{self._idx + 1} cooling down for 60s"
            )

            # Find the next key that is NOT cooling down
            for i in range(1, len(self._keys)):
                next_idx = (self._idx + i) % len(self._keys)
                if self._cooldowns.get(next_idx, 0) < now:
                    self._idx = next_idx
                    # Swap the API key on the parent instance.
                    # ChatGoogleGenerativeAI stores it as self.google_api_key
                    # and uses it via self.client (which is lazily created).
                    # We need to reset the client so it picks up the new key.
                    self.google_api_key = self._keys[next_idx]
                    # Force re-creation of the underlying genai Client
                    if hasattr(self, "_client"):
                        try:
                            delattr(self, "_client")
                        except Exception:
                            pass
                    print(
                        f"[multi_key_llm] 🔄 Rotated to key #{next_idx + 1}"
                        f"/{len(self._keys)}"
                    )
                    return True

            # All keys are cooling down — pick the one with the earliest expiry
            next_idx = min(
                range(len(self._keys)),
                key=lambda i: self._cooldowns.get(i, 0),
            )
            wait = max(0, self._cooldowns.get(next_idx, 0) - now)
            print(
                f"[multi_key_llm] ⚠️ All keys cooling down. "
                f"Using key #{next_idx + 1} (available in {wait:.0f}s)"
            )
            self._idx = next_idx
            self.google_api_key = self._keys[next_idx]
            if hasattr(self, "_client"):
                try:
                    delattr(self, "_client")
                except Exception:
                    pass
            return False

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            """Sync generate with automatic key rotation on 429."""
            max_retries = len(self._keys)
            last_error = None
            for attempt in range(max_retries):
                try:
                    return super()._generate(
                        messages, stop=stop, run_manager=run_manager, **kwargs
                    )
                except Exception as e:
                    last_error = e
                    if self._is_rate_limit_error(e) and attempt < max_retries - 1:
                        self._rotate_key()
                        continue
                    raise
            raise last_error

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
            """Async generate with automatic key rotation on 429."""
            max_retries = len(self._keys)
            last_error = None
            for attempt in range(max_retries):
                try:
                    return await super()._agenerate(
                        messages, stop=stop, run_manager=run_manager, **kwargs
                    )
                except Exception as e:
                    last_error = e
                    if self._is_rate_limit_error(e) and attempt < max_retries - 1:
                        self._rotate_key()
                        continue
                    raise
            raise last_error

    return MultiKeyGeminiLLM


# Module-level cache for the class
_CachedClass = None


def MultiKeyGeminiLLM(*args, **kwargs):
    """Factory function that creates a MultiKeyGeminiLLM instance.

    The actual class is built lazily on first call to avoid importing
    ChatGoogleGenerativeAI at module load time.
    """
    global _CachedClass
    if _CachedClass is None:
        _CachedClass = _make_multi_key_class()
    return _CachedClass(*args, **kwargs)


def get_gemini_keys() -> list:
    """Load Gemini API keys from config.

    Returns a list of keys. If 'gemini_api_keys' (plural) is present and
    non-empty, it takes precedence. Otherwise, falls back to the singular
    'gemini_api_key'.
    """
    import json
    from pathlib import Path
    import sys

    def _get_base_dir():
        if getattr(sys, "frozen", False):
            return Path(sys.executable).parent
        return Path(__file__).resolve().parent

    config_path = _get_base_dir() / "config" / "api_keys.json"
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return []

    # Prefer plural (list) if present
    keys = cfg.get("gemini_api_keys", [])
    if isinstance(keys, str):
        keys = [keys] if keys.strip() else []
    keys = [k.strip() for k in keys if k and k.strip()]

    # Fall back to singular
    if not keys:
        single = cfg.get("gemini_api_key", "").strip()
        if single:
            keys = [single]

    # Filter out placeholder values
    placeholders = {"your_gemini_api_key_here", ""}
    keys = [k for k in keys if k.lower() not in placeholders]

    return keys

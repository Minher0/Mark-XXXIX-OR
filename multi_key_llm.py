"""
multi_key_llm.py — Multi-key Gemini LLM wrapper with automatic key rotation.

When Google's free-tier rate limit (429) is hit on one API key, this wrapper
automatically rotates to the next key. With N keys from N different Google
Cloud projects, you get N× the daily quota.

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


class MultiKeyGeminiLLM:
    """Drop-in replacement for ChatGoogleGenerativeAI that rotates API keys on 429.

    Has `provider = 'google'` for browser-use compatibility.
    Delegates all LangChain methods to the underlying ChatGoogleGenerativeAI,
    rotating to the next key when the current one hits a 429 rate limit.

    Key rotation strategy:
      1. On 429, mark the current key as "cooling down" for 60 seconds
      2. Find the next key that is NOT cooling down
      3. Create a new ChatGoogleGenerativeAI with that key
      4. Retry the call
      5. If ALL keys are cooling down, use the one with the earliest cooldown
         expiry (effectively waiting until it's available)
    """

    provider = "google"  # browser-use checks this attribute

    def __init__(self, api_keys, model, **kwargs):
        if not api_keys:
            raise ValueError("MultiKeyGeminiLLM requires at least one API key")

        self._keys = list(api_keys)
        self._model = model
        self._kwargs = kwargs
        self._idx = 0
        self._cooldowns = {}  # key_index -> cooldown_until_timestamp
        self._bound_tools = None
        self._bound_kwargs = {}

        # Create the initial underlying LLM
        self._llm = self._create_llm(0)
        print(f"[multi_key_llm] 🔑 Initialized with {len(self._keys)} API key(s)")

    def _create_llm(self, idx):
        """Create a ChatGoogleGenerativeAI with the key at index idx."""
        from langchain_google_genai import ChatGoogleGenerativeAI
        return ChatGoogleGenerativeAI(
            model=self._model,
            google_api_key=self._keys[idx],
            **self._kwargs,
        )

    def _is_rate_limit_error(self, error) -> bool:
        """Check if an error is a 429 rate limit error."""
        err_str = str(error)
        return ("429" in err_str or
                "RESOURCE_EXHAUSTED" in err_str or
                "quota" in err_str.lower())

    def _rotate_key(self) -> bool:
        """Try to rotate to the next available key.

        Returns True if rotation succeeded, False if all keys are cooling down
        (in which case we use the one with the earliest cooldown expiry).
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
                self._llm = self._create_llm(next_idx)
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
        self._llm = self._create_llm(next_idx)
        return False

    def _get_bound_llm(self):
        """Return the underlying LLM, with tools bound if bind_tools was called."""
        if self._bound_tools is not None:
            return self._llm.bind_tools(
                self._bound_tools, **self._bound_kwargs
            )
        return self._llm

    # ── LangChain interface (delegates to underlying LLM with rotation) ──

    def bind_tools(self, tools, **kwargs):
        """Store the tools so we can re-bind when rotating keys.

        Returns self so that all subsequent ainvoke/invoke calls go through
        our rotation logic.
        """
        self._bound_tools = tools
        self._bound_kwargs = kwargs
        return self

    def with_structured_output(self, schema, **kwargs):
        """Delegate to the current LLM. Note: rotation won't work for
        structured output calls — but these are rare in browser-use."""
        return self._llm.with_structured_output(schema, **kwargs)

    async def ainvoke(self, messages, config=None, **kwargs):
        """Async invoke with automatic key rotation on 429."""
        max_retries = len(self._keys)
        for attempt in range(max_retries):
            try:
                bound = self._get_bound_llm()
                return await bound.ainvoke(messages, config=config, **kwargs)
            except Exception as e:
                if self._is_rate_limit_error(e) and attempt < max_retries - 1:
                    self._rotate_key()
                    continue
                raise

    def invoke(self, messages, config=None, **kwargs):
        """Sync invoke with automatic key rotation on 429."""
        max_retries = len(self._keys)
        for attempt in range(max_retries):
            try:
                bound = self._get_bound_llm()
                return bound.invoke(messages, config=config, **kwargs)
            except Exception as e:
                if self._is_rate_limit_error(e) and attempt < max_retries - 1:
                    self._rotate_key()
                    continue
                raise

    async def astream(self, messages, config=None, **kwargs):
        """Async stream with automatic key rotation on 429."""
        max_retries = len(self._keys)
        for attempt in range(max_retries):
            try:
                bound = self._get_bound_llm()
                async for chunk in bound.astream(messages, config=config, **kwargs):
                    yield chunk
                return  # streaming completed successfully
            except Exception as e:
                if self._is_rate_limit_error(e) and attempt < max_retries - 1:
                    self._rotate_key()
                    continue
                raise

    def stream(self, messages, config=None, **kwargs):
        """Sync stream with automatic key rotation on 429."""
        max_retries = len(self._keys)
        for attempt in range(max_retries):
            try:
                bound = self._get_bound_llm()
                yield from bound.stream(messages, config=config, **kwargs)
                return  # streaming completed successfully
            except Exception as e:
                if self._is_rate_limit_error(e) and attempt < max_retries - 1:
                    self._rotate_key()
                    continue
                raise

    @property
    def model_name(self):
        return self._model

    @property
    def _type(self):
        return "google-genai"


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

# actions/web_agent.py
# Autonomous web browsing agent powered by browser-use + Gemini.
#
# This module gives JARVIS the ability to accomplish complex multi-step web
# tasks autonomously, like:
#   - "Book me a flight from Paris to Tokyo next Friday"
#   - "Find and download the PDF for the paper 'Attention Is All You Need'"
#   - "Go to my Gmail and check unread emails from John"
#   - "Order me a pepperoni pizza from Domino's website"
#
# It uses the browser-use library (https://github.com/browser-use/browser-use/)
# which combines an LLM with Playwright to drive a real browser. The LLM sees
# screenshots of the page, decides what to click/type, and iterates until the
# task is done or max_steps is reached.
#
# Configuration (config/api_keys.json):
#   - gemini_api_key        (already used by other modules — required)
#   - web_agent_model       (optional, default: "gemini-2.5-flash")
#   - web_agent_max_steps   (optional, default: 30)
#   - web_agent_headless    (optional, default: false — user sees the browser)
#   - web_agent_use_vision  (optional, default: true — Gemini supports vision)
#
# Dependencies:
#   pip install browser-use langchain-google-genai
#   playwright install chromium

import asyncio
import json
import sys
import threading
import traceback
from pathlib import Path
from typing import Optional, Callable


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

DEFAULT_MODEL     = "gemini-2.0-flash"   # higher free-tier rate limit (60 RPM vs 20)
DEFAULT_FALLBACK_MODEL = "gemini-2.5-flash"  # used if primary is rate-limited
DEFAULT_MAX_STEPS = 30
DEFAULT_HEADLESS  = False    # user can see what JARVIS is doing
DEFAULT_VISION    = True     # Gemini 2.5 Flash supports vision
DEFAULT_KEEP_OPEN = True     # keep browser open after task so user can inspect
MAX_STEPS_CAP     = 100      # safety — prevent runaway agents
TASK_TIMEOUT_SEC  = 1200     # 20 minutes max per task (was 10 — heavy tasks need more)
RETRY_DELAY_ON_429 = 60     # seconds to wait when 429 hits before retrying


def _load_config() -> dict:
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _get_api_key() -> str:
    return _load_config().get("gemini_api_key", "").strip()


def _get_model() -> str:
    return _load_config().get("web_agent_model", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _get_max_steps() -> int:
    try:
        return int(_load_config().get("web_agent_max_steps", DEFAULT_MAX_STEPS))
    except (ValueError, TypeError):
        return DEFAULT_MAX_STEPS


def _get_headless() -> bool:
    val = _load_config().get("web_agent_headless", "false")
    return str(val).lower() in ("true", "1", "yes")


def _get_use_vision() -> bool:
    val = _load_config().get("web_agent_use_vision", "true")
    return str(val).lower() in ("true", "1", "yes")


def _get_keep_browser_open() -> bool:
    val = _load_config().get("web_agent_keep_browser_open", "true")
    return str(val).lower() in ("true", "1", "yes")


# ─── Helpers ───────────────────────────────────────────────

def _make_openrouter_llm_safe(or_model: str):
    """Create a LangChain-compatible LLM wrapper around OpenRouter.

    Uses langchain_openai.ChatOpenAI pointed at OpenRouter's API.
    Returns None on failure.
    """
    try:
        from or_client import _load_api_key as _or_load_key
        or_key = _or_load_key()
        if not or_key:
            return None
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=or_model,
            api_key=or_key,
            base_url="https://openrouter.ai/api/v1",
            temperature=0.1,
            default_headers={
                "HTTP-Referer": "https://github.com/mark-xxxix-or",
                "X-Title": "MARK XXXIX-OR",
            },
        )
    except ImportError:
        print("[web_agent] ⚠️ langchain-openai not installed — can't use OpenRouter fallback")
        return None
    except Exception as e:
        print(f"[web_agent] ⚠️ OpenRouter fallback init failed: {e}")
        return None


# ─── Async agent runner ────────────────────────────────────

async def _run_agent(
    task: str,
    max_steps: int,
    headless: bool,
    use_vision: bool,
    keep_browser_open: bool = True,
    speak: Optional[Callable] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Run the browser-use agent asynchronously.

    Args:
        keep_browser_open: if True (default), the browser stays open after the
            task completes so the user can inspect the result. The agent's
            asyncio task still terminates — the browser just remains visible
            until the user closes it manually.

    Returns the agent's final result as a string.
    """
    # Lazy imports so the module loads even if browser-use isn't installed.
    # browser-use's API has changed between versions — we try multiple import
    # patterns to stay compatible.
    Agent = None
    Browser = None
    BrowserConfig = None

    try:
        from browser_use import Agent  # noqa: F401
    except ImportError:
        return (
            "browser-use is not installed. Install with: pip install browser-use\n"
            "Then run: playwright install chromium"
        )

    # Browser and BrowserConfig have moved around between versions.
    # Try several known locations:
    #   v1.x: from browser_use import Browser, BrowserConfig
    #   v2.x: from browser_use.browser.browser import Browser
    #         from browser_use.browser.config import BrowserConfig
    #   Some versions: from browser_use.browser import Browser, BrowserConfig
    import_attempts = [
        ("browser_use",                "Browser",       "BrowserConfig"),
        ("browser_use.browser",        "Browser",       "BrowserConfig"),
        ("browser_use.browser.browser","Browser",       None),
    ]
    for mod_name, browser_attr, config_attr in import_attempts:
        try:
            mod = __import__(mod_name, fromlist=[browser_attr])
            Browser = getattr(mod, browser_attr, None)
            if Browser is None:
                continue
            if config_attr:
                BrowserConfig = getattr(mod, config_attr, None)
            else:
                # Try to import BrowserConfig from a sub-module
                try:
                    cfg_mod = __import__(f"{mod_name}.config", fromlist=["BrowserConfig"])
                    BrowserConfig = getattr(cfg_mod, "BrowserConfig", None)
                except (ImportError, AttributeError):
                    pass
            if Browser is not None:
                print(f"[web_agent] 📦 Using {mod_name} for Browser/BrowserConfig")
                break
        except ImportError:
            continue

    if Browser is None:
        return (
            "browser-use is installed but the Browser class could not be imported. "
            "Your version may be incompatible. Try: pip install --upgrade browser-use"
        )

    # If BrowserConfig is still None, just don't pass a config (use defaults)
    if BrowserConfig is None:
        print("[web_agent] ⚠️ BrowserConfig not found — using browser defaults")

    # ── Create the LLM ──
    # Check for multiple API keys first (quota pooling)
    try:
        from multi_key_llm import MultiKeyGeminiLLM, get_gemini_keys
        gemini_keys = get_gemini_keys()
    except ImportError:
        gemini_keys = []

    api_key = _get_api_key()
    if not api_key and not gemini_keys:
        return (
            "Gemini API key not configured. "
            "Add 'gemini_api_key' or 'gemini_api_keys' to config/api_keys.json."
        )

    model_name = _get_model()
    fallback_model_name = _load_config().get(
        "web_agent_fallback_model", DEFAULT_FALLBACK_MODEL
    ).strip() or DEFAULT_FALLBACK_MODEL
    or_fallback_model = _load_config().get(
        "web_agent_openrouter_fallback", ""
    ).strip()

    print(f"[web_agent] 🤖 LLM: {model_name} (vision={use_vision})")
    if len(gemini_keys) > 1:
        print(f"[web_agent] 🔑 Multi-key mode: {len(gemini_keys)} API keys pooled")
    print(f"[web_agent] 🤖 Fallback LLM: {fallback_model_name}")
    if or_fallback_model:
        print(f"[web_agent] 🤖 OpenRouter fallback: {or_fallback_model}")

    llm = None
    fallback_llm = None
    llm_errors = []
    _or_llm_cache = None

    # ── MULTI-KEY MODE: if we have 2+ keys, use MultiKeyGeminiLLM ──
    # This pools the daily quota across all keys, automatically rotating on 429
    if len(gemini_keys) >= 2:
        try:
            llm = MultiKeyGeminiLLM(
                api_keys=gemini_keys,
                model=model_name,
                temperature=0.1,
            )
            print(f"[web_agent] 📦 Using MultiKeyGeminiLLM ({len(gemini_keys)} keys)")

            # Also create a fallback with a different model, using the same keys
            if fallback_model_name and fallback_model_name != model_name:
                fallback_llm = MultiKeyGeminiLLM(
                    api_keys=gemini_keys,
                    model=fallback_model_name,
                    temperature=0.1,
                )
                print(f"[web_agent] 📦 Fallback MultiKeyGeminiLLM ready: {fallback_model_name}")

            # OpenRouter tertiary fallback
            if or_fallback_model:
                _or_llm_cache = _make_openrouter_llm_safe(or_fallback_model)
                if _or_llm_cache:
                    print(f"[web_agent] 📦 OpenRouter tertiary fallback ready")
        except Exception as e:
            print(f"[web_agent] ⚠️ MultiKeyGeminiLLM failed: {e}, falling back to single key")
            llm = None

    # ── SINGLE-KEY MODE (or multi-key failed) ──
    if llm is None:
        # Use the original single-key approach
        if not api_key and gemini_keys:
            api_key = gemini_keys[0]

        def _make_native_llm(model_name):
            for native_path in [
                ("browser_use.llm.google",       "ChatGoogle"),
                ("browser_use.llm.gemini",       "ChatGemini"),
                ("browser_use.llm",              "ChatGoogle"),
            ]:
                try:
                    mod = __import__(native_path[0], fromlist=[native_path[1]])
                    NativeCls = getattr(mod, native_path[1])
                    return NativeCls(model=model_name, api_key=api_key), native_path
                except (ImportError, AttributeError):
                    continue
                except Exception:
                    continue
            return None, None

        # Approach 1: browser-use's native ChatGoogle
        llm, native_path_used = _make_native_llm(model_name)
        if llm is not None:
            print(f"[web_agent] 📦 Using native LLM: {native_path_used[0]}.{native_path_used[1]}")
            if fallback_model_name and fallback_model_name != model_name:
                fallback_llm, _ = _make_native_llm(fallback_model_name)
                if fallback_llm:
                    print(f"[web_agent] 📦 Fallback LLM ready: {fallback_model_name}")

        # Approach 2: Subclass ChatGoogleGenerativeAI with provider attr
        if llm is None:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI

                class _GeminiWithProvider(ChatGoogleGenerativeAI):
                    provider: str = "google"
                    @property
                    def model_name(self) -> str:
                        return getattr(self, "model", "") or ""

                llm = _GeminiWithProvider(
                    model=model_name, google_api_key=api_key, temperature=0.1,
                )
                _ = llm.provider
                print(f"[web_agent] 📦 Using ChatGoogleGenerativeAI + provider wrapper")
                if fallback_model_name and fallback_model_name != model_name:
                    fallback_llm = _GeminiWithProvider(
                        model=fallback_model_name, google_api_key=api_key,
                        temperature=0.1,
                    )
            except ImportError:
                return ("langchain-google-genai is not installed. "
                        "Install with: pip install langchain-google-genai")
            except Exception as e:
                llm_errors.append(f"subclass approach: {e}")

        # Approach 3: Plain ChatGoogleGenerativeAI
        if llm is None:
            try:
                from langchain_google_genai import ChatGoogleGenerativeAI
                llm = ChatGoogleGenerativeAI(
                    model=model_name, google_api_key=api_key, temperature=0.1,
                )
                print("[web_agent] 📦 Using plain ChatGoogleGenerativeAI")
            except Exception as e:
                llm_errors.append(f"plain approach: {e}")

        if llm is None:
            return (
                "Could not initialise Gemini LLM for browser-use. Tried:\n  - " +
                "\n  - ".join(llm_errors) +
                "\n\nTry: pip install --upgrade browser-use langchain-google-genai"
            )

        # OpenRouter fallback for single-key mode
        if or_fallback_model and _or_llm_cache is None:
            _or_llm_cache = _make_openrouter_llm_safe(or_fallback_model)
            if _or_llm_cache:
                print(f"[web_agent] 📦 OpenRouter tertiary fallback ready")

    # Set up the browser
    try:
        if BrowserConfig is not None:
            # Build config kwargs — try to set keep_alive if the BrowserConfig
            # of this browser-use version supports it.
            cfg_kwargs = {
                "headless": headless,
                # Use a fresh profile so we don't interfere with the user's
                # main browser session (cookies, logins, etc.)
                "disable_security": False,
            }
            # Try to add keep_alive — not all browser-use versions support it
            # at the BrowserConfig level (older versions only have it on Agent)
            try:
                # Test if the parameter exists
                test_cfg = BrowserConfig(headless=headless, keep_alive=True)
                cfg_kwargs["keep_alive"] = keep_browser_open
            except (TypeError, Exception):
                # keep_alive not a valid param — skip it (will be handled below)
                pass

            browser_config = BrowserConfig(**cfg_kwargs)
            browser = Browser(config=browser_config)
        else:
            # Older/newer API without BrowserConfig — pass headless as kwarg
            # or use defaults
            try:
                browser = Browser(config=None)
            except TypeError:
                browser = Browser()
    except Exception as e:
        return f"Could not initialise browser: {e}"

    # ── Critical: prevent browser-use from closing the browser ──
    # When the agent finishes (calls the 'done' action), browser-use internally
    # triggers a BrowserStopEvent → session.reset(force=True) → session.close()
    # This happens BEFORE our finally block runs, so the browser is already
    # closed by the time we try to keep it open.
    #
    # Solution: monkey-patch the close() method on the Browser and its
    # BrowserSession to be no-ops when keep_browser_open=True. We keep a
    # reference to the original close method so we can force-close on
    # timeout/error.
    #
    # COMPATIBILITY NOTE: browser-use uses Pydantic v2 models with
    # validate_assignment=True, which means setting an attribute via
    # `obj.attr = value` triggers validation and rejects unknown attributes
    # (like 'close'). We bypass this by using object.__setattr__().
    _original_close_methods = []  # list of (obj, name, original_method)

    def _patch_method(obj, name, new_method):
        """Set an attribute on a (possibly Pydantic) object, bypassing validation.

        Tries three strategies in order:
          1. setattr() — works for non-Pydantic objects
          2. object.__setattr__() — bypasses Pydantic's __setattr__ override
          3. Class-level patch — sets the method on the class itself
             (affects all instances, but we accept this trade-off)

        Returns the original method (so we can restore it later) or None on
        failure.
        """
        original = getattr(obj, name, None)

        # Strategy 1: regular setattr (works for non-Pydantic)
        try:
            setattr(obj, name, new_method)
            return original
        except Exception:
            pass

        # Strategy 2: object.__setattr__ (bypasses Pydantic validation)
        try:
            object.__setattr__(obj, name, new_method)
            return original
        except (AttributeError, TypeError):
            pass

        # Strategy 3: class-level patch
        try:
            cls = type(obj)
            setattr(cls, name, new_method)
            # Note: this affects ALL instances, but for browser-use we only
            # run one agent at a time per process, so it's acceptable.
            return original
        except (AttributeError, TypeError):
            return None

    if keep_browser_open:
        async def _noop_close(*args, **kwargs):
            # Silently swallow close calls from browser-use internals
            pass

        # Patch browser.close
        orig = _patch_method(browser, "close", _noop_close)
        if orig is not None:
            _original_close_methods.append(("browser", "close", orig))
            print("[web_agent] 🔒 Patched browser.close → no-op")
        else:
            print("[web_agent] ⚠️ Could not patch browser.close")

        # Patch session.close and session.reset (browser-use v0.13+ uses
        # BrowserSession internally). Try multiple attribute names since the
        # API has changed across versions.
        for sess_attr in ("_session", "session", "_browser_session",
                          "browser_session", "_manager"):
            sess = getattr(browser, sess_attr, None)
            if sess is None:
                continue

            # Patch close()
            if hasattr(sess, "close"):
                orig_close = _patch_method(sess, "close", _noop_close)
                if orig_close is not None:
                    _original_close_methods.append((sess_attr, "close", orig_close))
                    print(f"[web_agent] 🔒 Patched {sess_attr}.close → no-op")

            # Patch reset() — this is the method that triggers close() when
            # the agent finishes. We force keep_alive=True so it doesn't close.
            if hasattr(sess, "reset"):
                original_reset = sess.reset

                async def _safe_reset(*args, _orig=original_reset, **kwargs):
                    # Force keep_alive=True so reset doesn't close the browser
                    kwargs["keep_alive"] = True
                    return await _orig(*args, **kwargs)

                if _patch_method(sess, "reset", _safe_reset) is not None:
                    print(f"[web_agent] 🔒 Patched {sess_attr}.reset → keep_alive=True")
            break  # only patch the first session found

    async def _force_close_browser():
        """Call the original close methods to actually close the browser.
        Used on timeout/error when we need to clean up."""
        for obj_name, method_name, original_method in _original_close_methods:
            try:
                # Find the current object reference
                if obj_name == "browser":
                    obj = browser
                else:
                    obj = getattr(browser, obj_name, None)
                if obj is None:
                    continue
                result = original_method()
                if hasattr(result, "__await__"):
                    await result
            except Exception:
                pass

    if speak:
        speak(f"Starting web agent, sir. Task: {task[:80]}")

    # Progress tracking — browser-use calls on_step after each step
    step_counter = [0]

    def _on_step(step):
        step_counter[0] = step
        if progress_callback:
            try:
                progress_callback(step, max_steps)
            except Exception:
                pass
        # Speak progress every 5 steps
        if speak and step % 5 == 0:
            speak(f"Step {step} of {max_steps}, sir.")

    # Build the agent — try with use_vision first, fall back if API changes.
    # Pass fallback_llm if available — browser-use v0.13+ supports it and will
    # automatically switch to it when the primary LLM returns 429.
    agent = None

    # Build a list of (kwargs_dict, label) to try in order, from most-features
    # to least. We don't pre-test with a fake agent because that's unreliable
    # (some kwargs are accepted at construction but only validated at run time).
    candidate_kwargs = [
        # Full kwargs (browser-use v0.13+)
        {
            "task": task, "llm": llm, "browser": browser,
            "use_vision": use_vision, "max_steps": max_steps,
            "fallback_llm": fallback_llm,
        },
        # Without fallback_llm
        {
            "task": task, "llm": llm, "browser": browser,
            "use_vision": use_vision, "max_steps": max_steps,
        },
        # Without max_steps
        {
            "task": task, "llm": llm, "browser": browser,
            "use_vision": use_vision,
            "fallback_llm": fallback_llm,
        },
        # Without use_vision
        {
            "task": task, "llm": llm, "browser": browser,
            "max_steps": max_steps,
            "fallback_llm": fallback_llm,
        },
        # Bare minimum
        {"task": task, "llm": llm, "browser": browser},
    ]

    for kwargs in candidate_kwargs:
        # Skip None values (e.g. fallback_llm might be None if creation failed)
        clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        try:
            agent = Agent(**clean_kwargs)
            accepted = list(clean_kwargs.keys())
            print(f"[web_agent] 📦 Agent created with kwargs: {accepted}")
            break
        except (TypeError, Exception) as e:
            continue

    if agent is None:
        try:
            await _force_close_browser()
        except Exception:
            pass
        return "Could not create agent (incompatible browser-use API)."

    print(f"[web_agent] 🌐 Running agent (max_steps={max_steps})...")

    def _result_contains_429(result) -> bool:
        """Check if the agent's result indicates a 429 quota error.

        browser-use v0.13 does NOT raise an exception on 429 — it logs
        'Stopping due to 5 consecutive failures' and returns an ActionResult
        with the error message in it. We have to inspect the result string.
        """
        try:
            result_str = str(result)
            return ("429" in result_str or
                    "RESOURCE_EXHAUSTED" in result_str or
                    "quota" in result_str.lower())
        except Exception:
            return False

    # Get the OpenRouter LLM cache (set during LLM init above, may be None)
    or_llm_for_retry = locals().get("_or_llm_cache", None)

    try:
        # Run with overall timeout. If the primary LLM hits 429, browser-use
        # retries 6 times rapidly, then stops and returns a result containing
        # the 429 error (without raising). We wrap the run in our own retry
        # loop that waits RETRY_DELAY_ON_429 seconds between attempts.
        #
        # On the 2nd retry, if an OpenRouter fallback LLM is available, we
        # recreate the agent with it as the PRIMARY llm — this handles the
        # case where Gemini's DAILY quota is exhausted (waiting won't help).
        result = None
        max_outer_retries = 3
        current_agent = agent
        for attempt in range(1, max_outer_retries + 1):
            try:
                result = await asyncio.wait_for(
                    current_agent.run(max_steps=max_steps),
                    timeout=TASK_TIMEOUT_SEC,
                )
                # Check if the result indicates a 429 (browser-use doesn't raise)
                if _result_contains_429(result) and attempt < max_outer_retries:
                    print(f"[web_agent] ⚠️ 429 quota error in result (attempt {attempt}/{max_outer_retries})")
                    # If we have an OpenRouter fallback and this is attempt 2+,
                    # recreate the agent with OpenRouter as the primary LLM.
                    # This handles the case where Gemini daily quota is gone.
                    if attempt == 2 and or_llm_for_retry is not None:
                        print(f"[web_agent] 🔄 Switching to OpenRouter LLM as primary "
                              f"(Gemini daily quota likely exhausted)")
                        if speak:
                            speak("Switching to OpenRouter, sir. Gemini quota is exhausted.")
                        try:
                            current_agent = Agent(
                                task=task,
                                llm=or_llm_for_retry,
                                browser=browser,
                                use_vision=False,  # OpenRouter free models usually don't support vision
                                max_steps=max_steps,
                            )
                            print("[web_agent] ✅ Agent recreated with OpenRouter LLM")
                        except Exception as e:
                            print(f"[web_agent] ⚠️ Could not recreate agent with OpenRouter: {e}")
                            # Fall back to waiting + retry with original agent
                            print(f"[web_agent]    Waiting {RETRY_DELAY_ON_429}s before retry...")
                            if speak:
                                speak(f"Hit the rate limit, sir. Waiting {RETRY_DELAY_ON_429} seconds.")
                            await asyncio.sleep(RETRY_DELAY_ON_429)
                        continue
                    else:
                        # Just wait and retry with the same agent
                        print(f"[web_agent]    Waiting {RETRY_DELAY_ON_429}s before retry...")
                        if speak:
                            speak(f"Hit the rate limit, sir. Waiting {RETRY_DELAY_ON_429} seconds before retrying.")
                        await asyncio.sleep(RETRY_DELAY_ON_429)
                        continue
                break  # success (or final retry)
            except asyncio.TimeoutError:
                raise  # let the outer except handle it
            except Exception as run_err:
                err_str = str(run_err)
                # Check if it's a 429 quota error (raised by some versions)
                if ("429" in err_str or "RESOURCE_EXHAUSTED" in err_str or
                    "quota" in err_str.lower()):
                    if attempt < max_outer_retries:
                        print(f"[web_agent] ⚠️ 429 quota error (attempt {attempt}/{max_outer_retries})")
                        if attempt == 2 and or_llm_for_retry is not None:
                            print(f"[web_agent] 🔄 Switching to OpenRouter LLM as primary")
                            if speak:
                                speak("Switching to OpenRouter, sir.")
                            try:
                                current_agent = Agent(
                                    task=task,
                                    llm=or_llm_for_retry,
                                    browser=browser,
                                    use_vision=False,
                                    max_steps=max_steps,
                                )
                            except Exception as e:
                                print(f"[web_agent] ⚠️ Could not recreate agent: {e}")
                                await asyncio.sleep(RETRY_DELAY_ON_429)
                        else:
                            print(f"[web_agent]    Waiting {RETRY_DELAY_ON_429}s before retry...")
                            if speak:
                                speak(f"Hit the rate limit, sir. Waiting {RETRY_DELAY_ON_429} seconds.")
                            await asyncio.sleep(RETRY_DELAY_ON_429)
                        continue
                # Not a 429, or last attempt — re-raise
                raise

        if result is None:
            return "Web agent did not produce a result after retries."

        # Extract the final result — browser-use API has evolved, so be flexible
        summary = None

        # Try final_result() method first (newer versions)
        try:
            summary = result.final_result()
        except (AttributeError, TypeError):
            pass

        # Try .final_result attribute
        if not summary and hasattr(result, "final_result"):
            try:
                fr = result.final_result
                if callable(fr):
                    summary = fr()
                else:
                    summary = str(fr)
            except Exception:
                pass

        # Fallback: look at history
        if not summary:
            try:
                if hasattr(result, "history") and result.history:
                    last = result.history[-1]
                    summary = str(getattr(last, "result", None) or last)
            except Exception:
                pass

        # Last resort
        if not summary:
            # If the result string contains the 429 error, surface it clearly
            try:
                result_str = str(result)
                if "429" in result_str or "RESOURCE_EXHAUSTED" in result_str:
                    summary = (
                        "Web agent hit the Gemini free-tier rate limit and could "
                        "not complete the task. This is a daily quota issue — "
                        "waiting won't help. Try again tomorrow, switch to a paid "
                        "Gemini plan, or use OpenRouter via the web_agent_fallback_model "
                        "config option. Original error: " + result_str[:500]
                    )
                else:
                    steps_done = step_counter[0]
                    summary = (
                        f"Web agent completed after {steps_done} step(s). "
                        "No explicit final result was returned."
                    )
            except Exception:
                summary = "Web agent completed with no final result."

        # Check for errors
        errors = []
        try:
            if hasattr(result, "errors") and result.errors:
                errors = list(result.errors)
        except Exception:
            pass

        if errors:
            err_str = "; ".join(str(e)[:100] for e in errors[:3])
            summary += f" (errors encountered: {err_str})"

        return summary.strip() or "Task completed."

    except asyncio.TimeoutError:
        # On timeout, force-close the browser regardless of keep_browser_open
        # (a hung task shouldn't leave a zombie browser running)
        try:
            await _force_close_browser()
        except Exception:
            pass
        if speak:
            speak("Web agent timed out, sir.")
        return (
            f"Web agent timed out after {TASK_TIMEOUT_SEC}s "
            f"(completed {step_counter[0]} steps)."
        )
    except Exception as e:
        traceback.print_exc()
        # On error, also close the browser
        try:
            await _force_close_browser()
        except Exception:
            pass
        if speak:
            speak("Web agent encountered an error, sir.")
        return f"Web agent failed: {e}"
    finally:
        # If the task succeeded and keep_browser_open is True, do NOT close
        # the browser — let the user inspect the final page manually.
        # The asyncio task will terminate normally; the browser process
        # keeps running until the user closes the window.
        #
        # Note: browser-use's internal close calls have been monkey-patched
        # to no-ops, so even if it tries to close the browser when the agent
        # finishes, nothing will happen. The browser stays open.
        if not keep_browser_open:
            try:
                await _force_close_browser()
            except Exception:
                pass
        else:
            print("[web_agent] 🌐 Browser left open for inspection (close manually when done)")
        if speak:
            speak("Web agent finished, sir.")


# ─── Synchronous entry point ──────────────────────────────

def web_agent(
    parameters: dict,
    response=None,
    player=None,
    speak: Optional[Callable] = None,
    session_memory=None,
) -> str:
    """Main entry point for the web_agent tool.

    Parameters (in `parameters` dict):
        task:               Natural language description of what to accomplish on the web.
        max_steps:          Optional limit on agent iterations (default: from config or 30).
        headless:           Optional bool — run browser without UI (default: false).
        use_vision:         Optional bool — use vision-capable LLM (default: true).
        keep_browser_open:  Optional bool — leave the browser open after the task
                            completes so the user can inspect the result
                            (default: true).

    Returns:
        A summary of what the agent accomplished.
    """
    params = parameters or {}
    task = (params.get("task") or params.get("goal") or "").strip()

    if not task:
        return "Please specify a task for the web agent."

    # Parse optional parameters
    try:
        max_steps = int(params.get("max_steps", _get_max_steps()))
    except (ValueError, TypeError):
        max_steps = _get_max_steps()
    max_steps = max(1, min(max_steps, MAX_STEPS_CAP))

    headless_param = params.get("headless")
    if headless_param is None:
        headless = _get_headless()
    else:
        headless = str(headless_param).lower() in ("true", "1", "yes")

    vision_param = params.get("use_vision")
    if vision_param is None:
        use_vision = _get_use_vision()
    else:
        use_vision = str(vision_param).lower() in ("true", "1", "yes")

    keep_open_param = params.get("keep_browser_open")
    if keep_open_param is None:
        keep_browser_open = _get_keep_browser_open()
    else:
        keep_browser_open = str(keep_open_param).lower() in ("true", "1", "yes")

    if player:
        player.write_log(f"[web_agent] starting: {task[:80]}")

    print(f"[web_agent] 🌐 Task: {task[:120]}")
    print(f"[web_agent]    max_steps={max_steps} headless={headless} vision={use_vision} keep_open={keep_browser_open}")

    # Progress callback — write to UI log
    def _progress(step: int, total: int):
        if player and step % 3 == 0:
            player.write_log(f"[web_agent] step {step}/{total}")

    # The web_agent runs in a worker thread (called via run_in_executor
    # in main.py). Inside that thread, we can safely create a new event
    # loop with asyncio.run().
    try:
        result = asyncio.run(_run_agent(
            task=task,
            max_steps=max_steps,
            headless=headless,
            use_vision=use_vision,
            keep_browser_open=keep_browser_open,
            speak=speak,
            progress_callback=_progress,
        ))
    except RuntimeError as e:
        # "asyncio.run() cannot be called from a running event loop"
        # → fall back to a fresh thread
        if "running event loop" in str(e):
            result_box = [None]
            exc_box = [None]

            def _runner():
                try:
                    result_box[0] = asyncio.run(_run_agent(
                        task=task,
                        max_steps=max_steps,
                        headless=headless,
                        use_vision=use_vision,
                        keep_browser_open=keep_browser_open,
                        speak=speak,
                        progress_callback=_progress,
                    ))
                except Exception as ex:
                    exc_box[0] = ex

            t = threading.Thread(target=_runner, name="web-agent-runner")
            t.start()
            t.join(timeout=TASK_TIMEOUT_SEC + 30)
            if t.is_alive():
                result = f"Web agent thread did not complete within {TASK_TIMEOUT_SEC + 30}s."
            elif exc_box[0]:
                result = f"Web agent thread crashed: {exc_box[0]}"
            else:
                result = result_box[0]
        else:
            raise

    print(f"[web_agent] ✅ Result: {str(result)[:200]}")
    if player:
        player.write_log(f"[web_agent] done: {str(result)[:80]}")

    return str(result)


# ─── Self-test ────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 55)
    print("  MARK XXXIX-OR — Web Agent Self-Test")
    print("=" * 55)

    test_task = "Go to google.com and search for 'python asyncio tutorial', then tell me the title of the first result."
    print(f"\nTask: {test_task}\n")

    result = web_agent(
        parameters={"task": test_task, "max_steps": 10},
        speak=lambda x: print(f"  [TTS] {x}"),
    )
    print(f"\nResult: {result}")

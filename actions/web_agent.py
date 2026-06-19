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

DEFAULT_MODEL     = "gemini-2.5-flash"
DEFAULT_MAX_STEPS = 30
DEFAULT_HEADLESS  = False    # user can see what JARVIS is doing
DEFAULT_VISION    = True     # Gemini 2.5 Flash supports vision
DEFAULT_KEEP_OPEN = True     # keep browser open after task so user can inspect
MAX_STEPS_CAP     = 100      # safety — prevent runaway agents
TASK_TIMEOUT_SEC  = 600      # 10 minutes max per task


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
    # browser-use v1+ checks `llm.provider` to know how to format messages
    # and call the model. ChatGoogleGenerativeAI from langchain-google-genai
    # doesn't expose this attribute by default → causes "object has no
    # attribute 'provider'" error. We try multiple approaches:
    #   1. browser_use.llm.google.ChatGoogle (native browser-use Gemini class)
    #   2. Subclass ChatGoogleGenerativeAI and add `provider = 'google'`
    #   3. Manual wrapper that delegates everything
    api_key = _get_api_key()
    if not api_key:
        return (
            "Gemini API key not configured. "
            "Add 'gemini_api_key' to config/api_keys.json."
        )

    model_name = _get_model()
    print(f"[web_agent] 🤖 LLM: {model_name} (vision={use_vision})")

    llm = None
    llm_errors = []

    # Approach 1: browser-use's native ChatGoogle class (cleanest)
    for native_path in [
        ("browser_use.llm.google",       "ChatGoogle"),
        ("browser_use.llm.gemini",       "ChatGemini"),
        ("browser_use.llm",              "ChatGoogle"),
    ]:
        try:
            mod = __import__(native_path[0], fromlist=[native_path[1]])
            NativeCls = getattr(mod, native_path[1])
            llm = NativeCls(model=model_name, api_key=api_key)
            print(f"[web_agent] 📦 Using native LLM: {native_path[0]}.{native_path[1]}")
            break
        except (ImportError, AttributeError):
            continue
        except Exception as e:
            llm_errors.append(f"{native_path[0]}.{native_path[1]}: {e}")
            continue

    # Approach 2: Subclass ChatGoogleGenerativeAI and add the `provider` attr
    if llm is None:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            class _GeminiWithProvider(ChatGoogleGenerativeAI):
                """ChatGoogleGenerativeAI with `provider` attr for browser-use v1+."""
                provider: str = "google"

                # Some browser-use versions also check `model_name` instead of `model`
                @property
                def model_name(self) -> str:
                    return getattr(self, "model", "") or ""

            llm = _GeminiWithProvider(
                model=model_name,
                google_api_key=api_key,
                temperature=0.1,
            )
            # Verify the provider attribute is actually accessible
            _ = llm.provider
            print(f"[web_agent] 📦 Using ChatGoogleGenerativeAI + provider='google' wrapper")
        except ImportError:
            return (
                "langchain-google-genai is not installed. "
                "Install with: pip install langchain-google-genai"
            )
        except Exception as e:
            llm_errors.append(f"subclass approach: {e}")

    # Approach 3: Last resort — try the basic ChatGoogleGenerativeAI without
    # the wrapper. Some older browser-use versions don't need `provider`.
    if llm is None:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
            llm = ChatGoogleGenerativeAI(
                model=model_name,
                google_api_key=api_key,
                temperature=0.1,
            )
            print("[web_agent] 📦 Using plain ChatGoogleGenerativeAI (fallback)")
        except Exception as e:
            llm_errors.append(f"plain approach: {e}")

    if llm is None:
        return (
            "Could not initialise Gemini LLM for browser-use. Tried:\n  - " +
            "\n  - ".join(llm_errors) +
            "\n\nTry: pip install --upgrade browser-use langchain-google-genai"
        )

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
    _original_close_methods = []  # list of (obj, name, original_method)

    if keep_browser_open:
        async def _noop_close(*args, **kwargs):
            # Silently swallow close calls from browser-use internals
            pass

        # Patch browser.close
        try:
            _original_close_methods.append(("browser", "close", browser.close))
            browser.close = _noop_close
            print("[web_agent] 🔒 Patched browser.close → no-op")
        except (AttributeError, TypeError) as e:
            print(f"[web_agent] ⚠️ Could not patch browser.close: {e}")

        # Patch session.close (browser-use v0.13+ uses BrowserSession internally)
        # Try multiple attribute names since the API has changed across versions
        for sess_attr in ("_session", "session", "_browser_session",
                          "browser_session", "_manager"):
            sess = getattr(browser, sess_attr, None)
            if sess is None:
                continue
            try:
                if hasattr(sess, "close"):
                    _original_close_methods.append(
                        (sess_attr, "close", sess.close)
                    )
                    sess.close = _noop_close
                    print(f"[web_agent] 🔒 Patched {sess_attr}.close → no-op")
            except (AttributeError, TypeError):
                continue

            # Also try to patch reset() to always pass keep_alive=True.
            # This is the method that actually triggers close() internally.
            if hasattr(sess, "reset"):
                try:
                    original_reset = sess.reset
                    async def _safe_reset(*args, _orig=original_reset, **kwargs):
                        # Force keep_alive=True so reset doesn't close the browser
                        kwargs["keep_alive"] = True
                        return await _orig(*args, **kwargs)
                    sess.reset = _safe_reset
                    print(f"[web_agent] 🔒 Patched {sess_attr}.reset → keep_alive=True")
                except (AttributeError, TypeError):
                    pass
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

    # Build the agent — try with use_vision first, fall back if API changes
    agent = None
    try:
        agent = Agent(
            task=task,
            llm=llm,
            browser=browser,
            use_vision=use_vision,
            max_steps=max_steps,
        )
    except TypeError:
        # Older browser-use versions don't have use_vision / max_steps
        try:
            agent = Agent(
                task=task,
                llm=llm,
                browser=browser,
            )
        except Exception as e:
            await browser.close()
            return f"Could not create agent: {e}"
    except Exception as e:
        await browser.close()
        return f"Could not create agent: {e}"

    print(f"[web_agent] 🌐 Running agent (max_steps={max_steps})...")

    try:
        # Run with overall timeout
        result = await asyncio.wait_for(
            agent.run(max_steps=max_steps),
            timeout=TASK_TIMEOUT_SEC,
        )

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
            steps_done = step_counter[0]
            summary = (
                f"Web agent completed after {steps_done} step(s). "
                "No explicit final result was returned."
            )

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

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


# ─── Async agent runner ────────────────────────────────────

async def _run_agent(
    task: str,
    max_steps: int,
    headless: bool,
    use_vision: bool,
    speak: Optional[Callable] = None,
    progress_callback: Optional[Callable[[int, int], None]] = None,
) -> str:
    """Run the browser-use agent asynchronously.

    Returns the agent's final result as a string.
    """
    # Lazy imports so the module loads even if browser-use isn't installed
    try:
        from browser_use import Agent, Browser, BrowserConfig
    except ImportError:
        return (
            "browser-use is not installed. Install with: pip install browser-use\n"
            "Then run: playwright install chromium"
        )

    try:
        from langchain_google_genai import ChatGoogleGenerativeAI
    except ImportError:
        return (
            "langchain-google-genai is not installed. "
            "Install with: pip install langchain-google-genai"
        )

    api_key = _get_api_key()
    if not api_key:
        return (
            "Gemini API key not configured. "
            "Add 'gemini_api_key' to config/api_keys.json."
        )

    # Set up the LLM (LangChain interface for browser-use)
    print(f"[web_agent] 🤖 LLM: {_get_model()} (vision={use_vision})")
    try:
        llm = ChatGoogleGenerativeAI(
            model=_get_model(),
            google_api_key=api_key,
            temperature=0.1,  # deterministic for web automation
        )
    except Exception as e:
        return f"Could not initialise LLM: {e}"

    # Set up the browser
    try:
        browser_config = BrowserConfig(
            headless=headless,
            # Use a fresh profile so we don't interfere with the user's
            # main browser session (cookies, logins, etc.)
            disable_security=False,
        )
        browser = Browser(config=browser_config)
    except Exception as e:
        return f"Could not initialise browser: {e}"

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
        return (
            f"Web agent timed out after {TASK_TIMEOUT_SEC}s "
            f"(completed {step_counter[0]} steps)."
        )
    except Exception as e:
        traceback.print_exc()
        return f"Web agent failed: {e}"
    finally:
        # Always close the browser
        try:
            await browser.close()
        except Exception:
            pass
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
        task:       Natural language description of what to accomplish on the web.
        max_steps:  Optional limit on agent iterations (default: from config or 30).
        headless:   Optional bool — run browser without UI (default: false).
        use_vision: Optional bool — use vision-capable LLM (default: true).

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

    if player:
        player.write_log(f"[web_agent] starting: {task[:80]}")

    print(f"[web_agent] 🌐 Task: {task[:120]}")
    print(f"[web_agent]    max_steps={max_steps} headless={headless} vision={use_vision}")

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

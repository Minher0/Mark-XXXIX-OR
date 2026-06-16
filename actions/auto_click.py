# auto_click.py — Smart multi-strategy auto-click action for Jarvis
# Tries: UI Automation → AI Vision → Browser Smart Click
# Supports: single click, repeated clicks with interval, click verification

import io
import re
import time
import json
import sys
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent

_BASE         = _base_dir()
_CONFIG_PATH  = _BASE / "config" / "api_keys.json"

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()

def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("pyautogui is not installed")


# ─── Strategy 1: UI Automation ──────────────────────────────────────

def _try_ui_click(target: str, element_type: str = "any", click_type: str = "left", index: int = 0) -> str | None:
    """Try to click using UI Automation (Windows/macOS/Linux)."""
    try:
        from actions.computer_control import _ui_click_element
        result = _ui_click_element(
            name=target,
            element_type=element_type,
            click_type=click_type,
            index=index,
        )
        # _ui_click_element returns "Element not found: ..." on failure
        if result and "not found" not in result.lower():
            return f"[UI Automation] {result}"
    except Exception as e:
        print(f"[AutoClick] UI Automation failed: {e}")
    return None


# ─── Strategy 2: AI Vision (screenshot → find → click) ─────────────

def _try_vision_click(target: str, click_type: str = "left") -> str | None:
    """Try to click using AI vision — screenshot + VLM to find element."""
    try:
        from actions.computer_control import _screen_find, _click
        coords = _screen_find(target)
        if coords:
            time.sleep(0.2)
            clicks = 2 if click_type == "double" else 1
            btn = "right" if click_type == "right" else "left"
            _click(x=coords[0], y=coords[1], button=btn, clicks=clicks)
            return f"[AI Vision] Clicked '{target}' at {coords}"
    except Exception as e:
        print(f"[AutoClick] AI Vision failed: {e}")
    return None


# ─── Strategy 3: Browser Smart Click (Playwright) ──────────────────

def _try_browser_click(target: str) -> str | None:
    """Try to click using Playwright browser smart_click."""
    try:
        from actions.browser_control import browser_control
        result = browser_control(parameters={
            "action": "smart_click",
            "description": target,
        })
        if result and "could not find" not in result.lower():
            return f"[Browser] {result}"
    except Exception as e:
        print(f"[AutoClick] Browser click failed: {e}")
    return None


# ─── Repeated Click Logic ──────────────────────────────────────────

def _repeat_click(
    target: str,
    count: int = 1,
    interval: float = 1.0,
    element_type: str = "any",
    click_type: str = "left",
    index: int = 0,
    strategy: str = "auto",
) -> str:
    """Click a target multiple times with interval between clicks.

    For the first click, uses the full strategy chain.
    For subsequent clicks, reuses the same coordinates if UI Automation was used,
    or re-runs the find to handle dynamic UIs.
    """
    results = []
    last_method = None
    last_coords = None

    for i in range(count):
        if i > 0:
            time.sleep(interval)

        clicked = False

        # If we found coordinates before, try clicking them directly (faster)
        if last_coords and last_method in ("ui_automation", "vision"):
            try:
                from actions.computer_control import _click
                clicks = 2 if click_type == "double" else 1
                btn = "right" if click_type == "right" else "left"
                _click(x=last_coords[0], y=last_coords[1], button=btn, clicks=clicks)
                results.append(f"[{i+1}] {last_method} at {last_coords}")
                clicked = True
            except Exception:
                last_coords = None  # Force re-find

        if not clicked:
            result = _auto_click_single(
                target=target,
                element_type=element_type,
                click_type=click_type,
                index=index,
                strategy=strategy,
            )
            results.append(f"[{i+1}] {result}")

            # Try to extract coordinates for repeat clicks
            coord_match = re.search(r'\((\d+)\s*,\s*(\d+)\)', result)
            if coord_match:
                last_coords = (int(coord_match.group(1)), int(coord_match.group(2)))
                if "[UI Automation]" in result:
                    last_method = "ui_automation"
                elif "[AI Vision]" in result:
                    last_method = "vision"
                elif "[Browser]" in result:
                    last_method = "browser"
                    last_coords = None  # Browser clicks can't be replayed by coords

    summary = f"Auto-clicked '{target}' {count}x (interval: {interval}s)\n"
    summary += "\n".join(results)
    return summary


# ─── Single Click — Strategy Chain ─────────────────────────────────

def _auto_click_single(
    target: str,
    element_type: str = "any",
    click_type: str = "left",
    index: int = 0,
    strategy: str = "auto",
) -> str:
    """Execute a single auto-click using the specified strategy.

    strategy options:
        - "auto"       : UI Automation → AI Vision → Browser (default)
        - "ui"         : UI Automation only
        - "vision"     : AI Vision only
        - "browser"    : Browser Smart Click only
        - "ui_vision"  : UI Automation → AI Vision (skip browser)
    """
    strategy = strategy.lower().strip()

    # Define the strategy chain
    if strategy == "auto":
        chain = [
            ("ui_automation", lambda: _try_ui_click(target, element_type, click_type, index)),
            ("vision",        lambda: _try_vision_click(target, click_type)),
            ("browser",       lambda: _try_browser_click(target)),
        ]
    elif strategy == "ui":
        chain = [
            ("ui_automation", lambda: _try_ui_click(target, element_type, click_type, index)),
        ]
    elif strategy == "vision":
        chain = [
            ("vision", lambda: _try_vision_click(target, click_type)),
        ]
    elif strategy == "browser":
        chain = [
            ("browser", lambda: _try_browser_click(target)),
        ]
    elif strategy == "ui_vision":
        chain = [
            ("ui_automation", lambda: _try_ui_click(target, element_type, click_type, index)),
            ("vision",        lambda: _try_vision_click(target, click_type)),
        ]
    else:
        return f"Unknown strategy: '{strategy}'. Use: auto | ui | vision | browser | ui_vision"

    # Execute strategy chain
    attempted = []
    for name, fn in chain:
        result = fn()
        if result:
            return result
        attempted.append(name)

    return f"Failed to click '{target}' — tried: {', '.join(attempted)}"


# ─── Public API ──────────────────────────────────────────────────────

def auto_click(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Smart auto-click action that tries multiple strategies to find and click
    a UI element. Supports single and repeated clicks.

    parameters:
        target       : Description or name of the element to click (required)
        strategy     : auto | ui | vision | browser | ui_vision (default: auto)
        click_type   : left | right | double (default: left)
        element_type : button | link | input | checkbox | tab | menu | any (default: any)
        index        : Index when multiple elements match (default: 0)
        count        : Number of times to click (default: 1)
        interval     : Seconds between repeated clicks (default: 1.0)
    """
    params = parameters or {}
    target = params.get("target", params.get("description", "")).strip()

    if not target:
        return "No target specified. Use 'target' parameter with element name or description."

    strategy     = params.get("strategy", "auto").strip().lower()
    click_type   = params.get("click_type", "left").strip().lower()
    element_type = params.get("element_type", "any").strip().lower()
    index        = int(params.get("index", 0))
    count        = int(params.get("count", 1))
    interval     = float(params.get("interval", 1.0))

    # Clamp values
    count    = max(1, min(count, 100))          # 1-100 clicks max
    interval = max(0.1, min(interval, 30.0))    # 0.1-30s interval

    if player:
        player.write_log(f"[auto_click] Target: '{target}' | Strategy: {strategy} | Count: {count}")

    try:
        if count == 1:
            result = _auto_click_single(
                target=target,
                element_type=element_type,
                click_type=click_type,
                index=index,
                strategy=strategy,
            )
        else:
            result = _repeat_click(
                target=target,
                count=count,
                interval=interval,
                element_type=element_type,
                click_type=click_type,
                index=index,
                strategy=strategy,
            )
    except Exception as e:
        result = f"auto_click failed: {e}"
        print(f"[AutoClick] Error: {e}")

    if player:
        player.write_log(f"[auto_click] {result[:80]}")

    return result

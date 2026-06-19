# auto_click.py — Smart multi-strategy auto-click action for Jarvis
# Priority: Browser (instant) → UI Automation (fast) → AI Vision (slow, last resort)
# Supports: single click, repeated clicks, spatial positioning

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


# ─── Spatial position mapping ──────────────────────────────────────

_POSITION_GRID = {
    # Numeric indices
    "1":    (0, 0), "2":    (0, 1), "3":    (0, 2),
    "4":    (1, 0), "5":    (1, 1), "6":    (1, 2),
    "7":    (2, 0), "8":    (2, 1), "9":    (2, 2),
    # French
    "premier":      (0, 0), "deuxieme":     (0, 1), "troisieme":    (0, 2),
    "quatrieme":    (1, 0), "cinquieme":    (1, 1), "sixieme":      (1, 2),
    "septieme":     (2, 0), "huitieme":     (2, 1), "neuvieme":     (2, 2),
    # English ordinal
    "first":    (0, 0), "second":   (0, 1), "third":    (0, 2),
    "fourth":   (1, 0), "fifth":    (1, 1), "sixth":    (1, 2),
    "seventh":  (2, 0), "eighth":   (2, 1), "ninth":    (2, 2),
    # Spatial
    "top-left":      (0, 0), "top-center":    (0, 1), "top-right":     (0, 2),
    "middle-left":   (1, 0), "center":        (1, 1), "middle-right":  (1, 2),
    "bottom-left":   (2, 0), "bottom-center": (2, 1), "bottom-right":  (2, 2),
    # Spatial (French)
    "haut-gauche":   (0, 0), "haut-milieu":   (0, 1), "haut-droite":   (0, 2),
    "milieu-gauche": (1, 0), "milieu":        (1, 1), "milieu-droite": (1, 2),
    "bas-gauche":    (2, 0), "bas-milieu":    (2, 1), "bas-droite":    (2, 2),
    # Short aliases
    "tl": (0, 0), "tc": (0, 1), "tr": (0, 2),
    "ml": (1, 0), "mc": (1, 1), "mr": (1, 2),
    "bl": (2, 0), "bc": (2, 1), "br": (2, 2),
}

# ─── Target → Playwright selector hints ────────────────────────────
# Maps common target descriptions to Playwright locator strategies
# These are tried FIRST because they're instant (no screenshot, no VLM)

_TARGET_SELECTORS = {
    # Videos / thumbnails (YouTube, etc.)
    "video":       ["ytd-thumbnail", "ytd-video-renderer", "ytd-grid-video-renderer",
                    "ytd-compact-video-renderer", "video", "[data-testid='video-card']"],
    "thumbnail":   ["ytd-thumbnail", "ytd-grid-video-renderer",
                    "img[src*='ytimg']", "[data-testid='thumbnail']"],
    # Links / buttons
    "link":        ["a"],
    "button":      ["button", "[role='button']", "input[type='submit']"],
    "image":       ["img"],
    "tab":         ["[role='tab']", ".tab"],
    "menu item":   ["[role='menuitem']", "li"],
    "card":        ["[class*='card']", "[data-testid*='card']"],
    "search":      ["input[type='search']", "[role='searchbox']", "input[name*='search']",
                    "input[placeholder*='Search']", "input[placeholder*='search']"],
    "input":       ["input[type='text']", "input:not([type])", "textarea",
                    "[contenteditable='true']"],
    "checkbox":    ["input[type='checkbox']", "[role='checkbox']"],
    "dropdown":    ["select", "[role='listbox']", "[role='combobox']"],
}


def _resolve_position(position: str, total_items: int) -> int | None:
    """Resolve a position string to a 0-based index among total_items."""
    pos = position.strip().lower()

    if pos.isdigit():
        idx = int(pos) - 1
        return idx if 0 <= idx < total_items else None

    grid_pos = _POSITION_GRID.get(pos)
    if grid_pos is None:
        ordinal_match = re.match(r'(\d+)(?:st|nd|rd|th)', pos)
        if ordinal_match:
            idx = int(ordinal_match.group(1)) - 1
            return idx if 0 <= idx < total_items else None
        return None

    row, col = grid_pos

    if total_items <= 1:
        return 0
    elif total_items <= 3:
        cols = total_items
        idx = col if col < cols else cols - 1
        return idx
    elif total_items <= 4:
        ncols = 2
        idx = row * ncols + col
        return idx if idx < total_items else total_items - 1
    elif total_items <= 6:
        ncols = 3
        idx = row * ncols + col
        if row >= 2:
            idx = 1 * ncols + col
        return idx if idx < total_items else total_items - 1
    elif total_items <= 9:
        ncols = 3
        idx = row * ncols + col
        return idx if idx < total_items else total_items - 1
    else:
        ncols = 3
        idx = row * ncols + col
        return min(idx, total_items - 1)


def _is_positional(pos: str) -> bool:
    """Check if a string is a recognizable position keyword."""
    if not pos:
        return False
    p = pos.strip().lower()
    return (
        p in _POSITION_GRID
        or p.isdigit()
        or re.match(r'\d+(?:st|nd|rd|th)', p) is not None
    )


# ═══════════════════════════════════════════════════════════════════
# STRATEGY 1: Browser Spatial Click — INSTANT (no screenshot, no VLM)
# ═══════════════════════════════════════════════════════════════════

def _try_browser_spatial_click(target: str, position: str = "", click_type: str = "left") -> str | None:
    """Find all matching elements in the browser DOM and click the Nth one.

    Uses Playwright locators — no screenshot, no VLM, instant.
    Tries multiple selector strategies based on the target description.
    """
    try:
        from actions.browser_control import _bt, _ensure_started
        _ensure_started()

        page = _bt.run(_bt._get_page(), timeout=10)
        if not page:
            return None

        # Build list of selectors to try
        selectors = _TARGET_SELECTORS.get(target.lower(), [])

        # Also try text/role-based matching as additional selectors
        extra_selectors = [
            # Get by text (most flexible)
            f"text={target}",
        ]
        # Don't add extras if target is too generic
        if target.lower() not in _TARGET_SELECTORS:
            selectors = extra_selectors
        else:
            selectors = selectors + extra_selectors

        for selector in selectors:
            try:
                locator = page.locator(selector)
                count = locator.count()

                if count == 0:
                    continue

                # Resolve position
                idx = 0
                if position:
                    idx = _resolve_position(position, count)
                    if idx is None:
                        idx = 0

                if idx >= count:
                    idx = count - 1

                # Click the Nth element
                element = locator.nth(idx)
                element.click(timeout=5000)

                label = f"'{target}' ({selector})"
                if position:
                    return f"[Browser] Clicked #{idx+1} {label} (out of {count} found)"
                return f"[Browser] Clicked {label}"

            except Exception:
                continue

    except Exception as e:
        print(f"[AutoClick] Browser spatial failed: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════
# STRATEGY 2: UI Automation — FAST (OS-level, no screenshot)
# ═══════════════════════════════════════════════════════════════════

def _try_ui_click(target: str, element_type: str = "any", click_type: str = "left", index: int = 0) -> str | None:
    """Try to click using UI Automation (Windows/macOS/Linux). ~0.5s."""
    try:
        from actions.computer_control import _ui_click_element
        result = _ui_click_element(
            name=target,
            element_type=element_type,
            click_type=click_type,
            index=index,
        )
        if result and "not found" not in result.lower():
            return f"[UI Automation] {result}"
    except Exception as e:
        print(f"[AutoClick] UI Automation failed: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════
# STRATEGY 3: Browser Smart Click — FAST (Playwright, no VLM)
# ═══════════════════════════════════════════════════════════════════

def _try_browser_click(target: str) -> str | None:
    """Try to click using Playwright browser smart_click. ~1s."""
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


# ═══════════════════════════════════════════════════════════════════
# STRATEGY 4: AI Vision — SLOW (last resort only)
# ═══════════════════════════════════════════════════════════════════

def _try_vision_click(target: str, click_type: str = "left") -> str | None:
    """Try to click using AI vision. 3-8s. LAST RESORT."""
    try:
        from actions.computer_control import _screen_find, _click
        coords = _screen_find(target)
        if coords:
            time.sleep(0.15)
            clicks = 2 if click_type == "double" else 1
            btn = "right" if click_type == "right" else "left"
            _click(x=coords[0], y=coords[1], button=btn, clicks=clicks)
            return f"[AI Vision] Clicked '{target}' at {coords}"
    except Exception as e:
        print(f"[AutoClick] AI Vision failed: {e}")
    return None


def _try_vision_spatial_click(target: str, position: str, click_type: str = "left") -> str | None:
    """Find ALL instances via VLM, then click the one at the specified position. 5-10s. LAST RESORT."""
    items = _vision_find_all(target)
    if not items:
        return None

    total = len(items)
    idx = _resolve_position(position, total)
    if idx is None:
        return f"Position '{position}' is out of range. Found {total} items — use 1 to {total}."

    item = items[idx]

    try:
        from actions.computer_control import _click
        time.sleep(0.15)
        clicks = 2 if click_type == "double" else 1
        btn = "right" if click_type == "right" else "left"
        _click(x=item["x"], y=item["y"], button=btn, clicks=clicks)
    except Exception as e:
        return f"[Vision Spatial] Found item but click failed: {e}"

    return (
        f"[Vision Spatial] Clicked #{idx+1} '{item['label']}' at ({item['x']}, {item['y']}) "
        f"out of {total} found"
    )


def _vision_find_all(target: str) -> list[dict] | None:
    """Use AI vision to find ALL instances of a target element on screen."""
    try:
        import base64
        from or_client import client

        _require_pyautogui()
        w, h  = pyautogui.size()
        img   = pyautogui.screenshot()
        buf   = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        prompt = (
            f"This is a screenshot of a {w}x{h} pixel screen. "
            f"Find ALL instances of: '{target}'. "
            f"For each instance, provide its center coordinates (x, y) and a short label. "
            f"Sort them left-to-right, top-to-bottom (reading order). "
            f"Reply ONLY with valid JSON array, no other text. "
            f'Format: [{{"index": 1, "x": 123, "y": 456, "label": "video thumbnail"}}, ...] '
            f"If nothing found, reply: []"
        )

        text = client.vision(
            prompt,
            image_b64=b64,
            mime="image/png",
            system="You are a precise UI element locator. Return ONLY the JSON array, no markdown, no explanation.",
        )

        clean = text.strip()
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip().rstrip("`").strip()

        items = json.loads(clean)
        if not isinstance(items, list) or len(items) == 0:
            return None

        results = []
        for i, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            x = item.get("x")
            y = item.get("y")
            if x is None or y is None:
                continue
            results.append({
                "index": i,
                "x": int(x),
                "y": int(y),
                "label": item.get("label", f"{target} #{i+1}"),
            })

        results.sort(key=lambda el: (el["y"], el["x"]))

        for i, el in enumerate(results):
            el["index"] = i

        return results if results else None

    except json.JSONDecodeError:
        # Fallback to single-element find
        return _vision_find_all_fallback(target)
    except Exception as e:
        print(f"[AutoClick] Vision grid find failed: {e}")
    return None


def _vision_find_all_fallback(target: str) -> list[dict] | None:
    """Fallback: use _screen_find and return a single-item list."""
    try:
        from actions.computer_control import _screen_find
        coords = _screen_find(target)
        if coords:
            return [{"index": 0, "x": coords[0], "y": coords[1], "label": target}]
    except Exception as e:
        print(f"[AutoClick] Vision fallback failed: {e}")
    return None


# ═══════════════════════════════════════════════════════════════════
# REPEATED CLICK LOGIC
# ═══════════════════════════════════════════════════════════════════

def _repeat_click(
    target: str,
    count: int = 1,
    interval: float = 1.0,
    element_type: str = "any",
    click_type: str = "left",
    index: int = 0,
    strategy: str = "auto",
    position: str = "",
) -> str:
    """Click a target multiple times with interval between clicks."""
    results = []
    last_method = None
    last_coords = None
    last_selector = None
    last_idx = None

    for i in range(count):
        if i > 0:
            time.sleep(interval)

        clicked = False

        # Reuse coords for fast repeat clicks (UI Automation / Vision)
        if last_coords and last_method in ("ui_automation", "vision", "vision_spatial"):
            try:
                from actions.computer_control import _click
                clicks = 2 if click_type == "double" else 1
                btn = "right" if click_type == "right" else "left"
                _click(x=last_coords[0], y=last_coords[1], button=btn, clicks=clicks)
                results.append(f"[{i+1}] {last_method} at {last_coords}")
                clicked = True
            except Exception:
                last_coords = None

        # Reuse browser selector for fast repeat clicks
        elif last_method == "browser" and last_selector and last_idx is not None:
            try:
                from actions.browser_control import _bt
                page = _bt.run(_bt._get_page(), timeout=5)
                if page:
                    locator = page.locator(last_selector)
                    if locator.count() > last_idx:
                        locator.nth(last_idx).click(timeout=3000)
                        results.append(f"[{i+1}] browser repeat")
                        clicked = True
            except Exception:
                pass

        if not clicked:
            result = _auto_click_single(
                target=target,
                element_type=element_type,
                click_type=click_type,
                index=index,
                strategy=strategy,
                position=position,
            )
            results.append(f"[{i+1}] {result}")

            # Cache for repeat clicks
            coord_match = re.search(r'\((\d+)\s*,\s*(\d+)\)', result)
            if coord_match:
                last_coords = (int(coord_match.group(1)), int(coord_match.group(2)))
                if "[UI Automation]" in result:
                    last_method = "ui_automation"
                elif "[Vision Spatial]" in result or "[AI Vision]" in result:
                    last_method = "vision"
            elif "[Browser]" in result:
                last_method = "browser"
                last_coords = None
                # Try to extract the selector used
                for sel_list in _TARGET_SELECTORS.values():
                    for sel in sel_list:
                        if sel in result:
                            last_selector = sel
                            last_idx = 0
                            break

    summary = f"Auto-clicked '{target}' {count}x (interval: {interval}s)\n"
    summary += "\n".join(results)
    return summary


# ═══════════════════════════════════════════════════════════════════
# SINGLE CLICK — STRATEGY CHAIN (ordered by speed)
# ═══════════════════════════════════════════════════════════════════

def _auto_click_single(
    target: str,
    element_type: str = "any",
    click_type: str = "left",
    index: int = 0,
    strategy: str = "auto",
    position: str = "",
) -> str:
    """Execute a single auto-click.

    Strategy priority (when position is set):
      1. Browser Spatial  — INSTANT (Playwright DOM query, .nth(idx).click())
      2. UI Automation    — FAST   (OS-level element find)
      3. Vision Spatial   — SLOW   (screenshot + VLM, last resort)

    Strategy priority (when NO position):
      1. Browser Smart Click — FAST (Playwright role/text/placeholder)
      2. UI Automation       — FAST (OS-level)
      3. AI Vision           — SLOW (screenshot + VLM)
    """
    strategy = strategy.lower().strip()
    has_position = bool(position) and _is_positional(position)

    # ─── Positional mode: prefer browser DOM queries (instant) ──────
    if has_position:
        # 1) Browser spatial — instant, no screenshot
        result = _try_browser_spatial_click(target, position, click_type)
        if result:
            return result

        # 2) UI Automation — fast, no screenshot
        idx = _resolve_position(position, 20) or 0
        result = _try_ui_click(target, element_type, click_type, idx)
        if result:
            return result

        # 3) Vision spatial — slow, last resort
        result = _try_vision_spatial_click(target, position, click_type)
        if result:
            return result

        return f"Failed to click '{target}' at position '{position}'"

    # ─── Non-positional mode: standard strategy chain ──────────────
    if strategy == "auto":
        # 1) Browser — instant for web pages
        # 2) UI Automation — fast for desktop apps
        # 3) Vision — slow, last resort
        chain = [
            ("browser",       lambda: _try_browser_click(target)),
            ("ui_automation", lambda: _try_ui_click(target, element_type, click_type, index)),
            ("vision",        lambda: _try_vision_click(target, click_type)),
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
    elif strategy == "spatial":
        result = _try_browser_spatial_click(target, position or "1", click_type)
        if result:
            return result
        result = _try_vision_spatial_click(target, position or "1", click_type)
        if result:
            return result
        return f"Failed to click '{target}' in spatial mode"
    else:
        return f"Unknown strategy: '{strategy}'. Use: auto | ui | vision | browser | ui_vision | spatial"

    attempted = []
    for name, fn in chain:
        result = fn()
        if result:
            return result
        attempted.append(name)

    return f"Failed to click '{target}' — tried: {', '.join(attempted)}"


# ═══════════════════════════════════════════════════════════════════
# PUBLIC API
# ═══════════════════════════════════════════════════════════════════

def auto_click(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Smart auto-click — fast first, VLM last.

    Priority: Browser (instant) → UI Automation (fast) → AI Vision (slow)

    parameters:
        target       : Element to click (required)
        position     : Which instance: '1'-'9', 'first', '3rd', 'top-left', 'deuxieme', etc.
        strategy     : auto | ui | vision | browser | ui_vision | spatial (default: auto)
        click_type   : left | right | double (default: left)
        element_type : button | link | input | checkbox | tab | menu | any (default: any)
        index        : Index when multiple elements match (default: 0)
        count        : Number of clicks (default: 1, max: 100)
        interval     : Seconds between repeated clicks (default: 1.0)
    """
    params = parameters or {}
    target = params.get("target", params.get("description", "")).strip()

    if not target:
        return "No target specified. Use 'target' parameter."

    strategy     = params.get("strategy", "auto").strip().lower()
    click_type   = params.get("click_type", "left").strip().lower()
    element_type = params.get("element_type", "any").strip().lower()
    index        = int(params.get("index", 0))
    count        = int(params.get("count", 1))
    interval     = float(params.get("interval", 1.0))
    position     = params.get("position", "").strip()

    count    = max(1, min(count, 100))
    interval = max(0.1, min(interval, 30.0))

    if player:
        pos_info = f" | Pos: {position}" if position else ""
        player.write_log(f"[auto_click] '{target}'{pos_info} | {strategy} | x{count}")

    try:
        if count == 1:
            result = _auto_click_single(
                target=target,
                element_type=element_type,
                click_type=click_type,
                index=index,
                strategy=strategy,
                position=position,
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
                position=position,
            )
    except Exception as e:
        result = f"auto_click failed: {e}"
        print(f"[AutoClick] Error: {e}")

    if player:
        player.write_log(f"[auto_click] {result[:80]}")

    return result

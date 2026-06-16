# auto_click.py — Smart multi-strategy auto-click action for Jarvis
# Tries: UI Automation → AI Vision → Browser Smart Click
# Supports: single click, repeated clicks, spatial positioning (grid mode)

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
# Maps position keywords to grid indices (row, col) in a conceptual 3x3 grid

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


def _resolve_position(position: str, total_items: int) -> int | None:
    """Resolve a position string to a 0-based index among total_items.

    Supports:
      - Numeric: "1" to "9" (1-based → 0-based)
      - Spatial: "top-left", "center", "bottom-right", etc.
      - Ordinal: "first", "3rd", "deuxieme", etc.
      - Auto-layout: maps grid position to linear index based on item count
    """
    pos = position.strip().lower()

    # Direct numeric index (1-based)
    if pos.isdigit():
        idx = int(pos) - 1
        return idx if 0 <= idx < total_items else None

    # Grid position mapping
    grid_pos = _POSITION_GRID.get(pos)
    if grid_pos is None:
        # Try to extract ordinal number: "3rd", "2nd", "1st"
        ordinal_match = re.match(r'(\d+)(?:st|nd|rd|th)', pos)
        if ordinal_match:
            idx = int(ordinal_match.group(1)) - 1
            return idx if 0 <= idx < total_items else None
        return None

    row, col = grid_pos

    # Determine grid dimensions based on total items
    if total_items <= 1:
        return 0
    elif total_items <= 3:
        # 1 row layout: items go left to right
        cols = total_items
        idx = col if col < cols else cols - 1
        return idx
    elif total_items <= 4:
        # 2x2 grid
        ncols = 2
        idx = row * ncols + col
        # Adjust for items that don't fill the grid
        return idx if idx < total_items else total_items - 1
    elif total_items <= 6:
        # 2x3 or 3x2 grid — use 2 rows, 3 cols for 6 items
        ncols = 3
        idx = row * ncols + col
        # If 2-row layout, only rows 0-1 are valid
        if row >= 2:
            # Remap to bottom row
            idx = 1 * ncols + col
        return idx if idx < total_items else total_items - 1
    elif total_items <= 9:
        # 3x3 grid
        ncols = 3
        idx = row * ncols + col
        return idx if idx < total_items else total_items - 1
    else:
        # More than 9 items: use row-based layout with 3 cols
        ncols = 3
        nrows = (total_items + ncols - 1) // ncols
        idx = row * ncols + col
        return min(idx, total_items - 1)


# ─── Vision Grid: find ALL matching elements on screen ─────────────

def _vision_find_all(target: str) -> list[dict] | None:
    """Use AI vision to find ALL instances of a target element on screen.

    Returns a list of dicts: [{"index": 0, "x": 123, "y": 456, "label": "..."}, ...]
    Sorted left-to-right, top-to-bottom.
    """
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

        # Parse JSON from VLM response
        clean = text.strip()
        # Strip markdown fences if present
        if clean.startswith("```"):
            parts = clean.split("```")
            clean = parts[1] if len(parts) > 1 else clean
            if clean.startswith("json"):
                clean = clean[4:]
        clean = clean.strip().rstrip("`").strip()

        items = json.loads(clean)
        if not isinstance(items, list) or len(items) == 0:
            return None

        # Validate and normalize items
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

        # Sort by position: top-to-bottom, then left-to-right
        results.sort(key=lambda el: (el["y"], el["x"]))

        # Re-index after sorting
        for i, el in enumerate(results):
            el["index"] = i

        return results if results else None

    except json.JSONDecodeError as e:
        print(f"[AutoClick] Vision grid JSON parse failed: {e}")
        # Fallback: try to extract coordinates from free-text response
        return _vision_find_all_fallback(target)
    except Exception as e:
        print(f"[AutoClick] Vision grid find failed: {e}")
    return None


def _vision_find_all_fallback(target: str) -> list[dict] | None:
    """Fallback: use the existing _screen_find and return a single-item list."""
    try:
        from actions.computer_control import _screen_find
        coords = _screen_find(target)
        if coords:
            return [{"index": 0, "x": coords[0], "y": coords[1], "label": target}]
    except Exception as e:
        print(f"[AutoClick] Vision fallback failed: {e}")
    return None


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


# ─── Spatial Click — Vision Grid Mode ──────────────────────────────

def _try_spatial_click(target: str, position: str, click_type: str = "left") -> str | None:
    """Find ALL instances of target, then click the one at the specified position.

    Uses AI vision to locate all matching elements, sorts them in reading order,
    then selects the one at the given position (index or spatial keyword).
    """
    items = _vision_find_all(target)
    if not items:
        return None

    total = len(items)
    idx = _resolve_position(position, total)

    if idx is None:
        return f"Position '{position}' is out of range. Found {total} items — use 1 to {total}."

    item = items[idx]

    # Click the selected item
    try:
        from actions.computer_control import _click
        time.sleep(0.2)
        clicks = 2 if click_type == "double" else 1
        btn = "right" if click_type == "right" else "left"
        _click(x=item["x"], y=item["y"], button=btn, clicks=clicks)
    except Exception as e:
        return f"[Spatial] Found item but click failed: {e}"

    # Build summary
    found_summary = ", ".join(
        f"#{i+1}: {el['label']} ({el['x']},{el['y']})"
        for i, el in enumerate(items)
    )
    return (
        f"[Spatial] Clicked #{idx+1} '{item['label']}' at ({item['x']}, {item['y']}) "
        f"out of {total} found: [{found_summary}]"
    )


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
    """Click a target multiple times with interval between clicks."""
    results = []
    last_method = None
    last_coords = None

    for i in range(count):
        if i > 0:
            time.sleep(interval)

        clicked = False

        if last_coords and last_method in ("ui_automation", "vision", "spatial"):
            try:
                from actions.computer_control import _click
                clicks = 2 if click_type == "double" else 1
                btn = "right" if click_type == "right" else "left"
                _click(x=last_coords[0], y=last_coords[1], button=btn, clicks=clicks)
                results.append(f"[{i+1}] {last_method} at {last_coords}")
                clicked = True
            except Exception:
                last_coords = None

        if not clicked:
            result = _auto_click_single(
                target=target,
                element_type=element_type,
                click_type=click_type,
                index=index,
                strategy=strategy,
            )
            results.append(f"[{i+1}] {result}")

            coord_match = re.search(r'\((\d+)\s*,\s*(\d+)\)', result)
            if coord_match:
                last_coords = (int(coord_match.group(1)), int(coord_match.group(2)))
                if "[UI Automation]" in result:
                    last_method = "ui_automation"
                elif "[AI Vision]" in result:
                    last_method = "vision"
                elif "[Spatial]" in result:
                    last_method = "spatial"
                elif "[Browser]" in result:
                    last_method = "browser"
                    last_coords = None

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
    position: str = "",
) -> str:
    """Execute a single auto-click using the specified strategy.

    If `position` is provided, uses spatial grid mode (AI vision finds all
    instances, then selects the one at the given position).
    """
    strategy = strategy.lower().strip()

    # ─── Spatial mode: position parameter triggers grid search ────
    if position:
        pos_lower = position.strip().lower()
        # Check if it's a recognizable position keyword
        is_positional = (
            pos_lower in _POSITION_GRID
            or pos_lower.isdigit()
            or re.match(r'\d+(?:st|nd|rd|th)', pos_lower)
            or pos_lower in ("first", "second", "third", "fourth", "fifth",
                             "sixth", "seventh", "eighth", "ninth",
                             "premier", "deuxieme", "troisieme", "quatrieme",
                             "cinquieme", "sixieme", "septieme", "huitieme", "neuvieme")
        )

        if is_positional:
            # Spatial grid mode: vision finds all, then we pick by position
            result = _try_spatial_click(target, position, click_type)
            if result:
                return result
            return f"Failed to click '{target}' at position '{position}' — could not locate elements on screen"

    # ─── Normal strategy chain ────────────────────────────────────
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
    elif strategy == "spatial":
        # Force spatial mode even without position (clicks first found)
        result = _try_spatial_click(target, position or "1", click_type)
        if result:
            return result
        return f"Failed to click '{target}' in spatial mode — could not locate elements on screen"
    else:
        return f"Unknown strategy: '{strategy}'. Use: auto | ui | vision | browser | ui_vision | spatial"

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
    a UI element. Supports single/repeated clicks and spatial positioning.

    parameters:
        target       : Description or name of the element to click (required)
        position     : Position among multiple matches: 1-9, "3rd", "top-left",
                       "center", "bottom-right", "deuxieme", etc.
                       Triggers spatial grid mode (AI vision finds all instances).
        strategy     : auto | ui | vision | browser | ui_vision | spatial (default: auto)
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
    position     = params.get("position", "").strip()

    # Clamp values
    count    = max(1, min(count, 100))
    interval = max(0.1, min(interval, 30.0))

    if player:
        pos_info = f" | Position: {position}" if position else ""
        player.write_log(f"[auto_click] Target: '{target}'{pos_info} | Strategy: {strategy} | Count: {count}")

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
            )
    except Exception as e:
        result = f"auto_click failed: {e}"
        print(f"[AutoClick] Error: {e}")

    if player:
        player.write_log(f"[auto_click] {result[:80]}")

    return result

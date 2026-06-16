#computer_control.py
import io
import json
import re
import string
import subprocess
import sys
import time
import random
from pathlib import Path

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE    = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False

try:
    import pyperclip
    _PYPERCLIP = True
except ImportError:
    _PYPERCLIP = False

def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


_BASE         = _base_dir()
_CONFIG_PATH  = _BASE / "config" / "api_keys.json"
_MEMORY_PATH  = _BASE / "memory" / "long_term.json"

def _load_config() -> dict:
    try:
        return json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _get_os() -> str:
    return _load_config().get("os_system", "windows").lower()

_SAFE_SCREENSHOT_ROOTS = (
    Path.home(),
)

def _safe_screenshot_path(requested: str | None) -> Path:
    fallback = Path.home() / "Desktop" / "jarvis_screenshot.png"
    if not requested:
        return fallback
    try:
        p = Path(requested).expanduser().resolve()
        for root in _SAFE_SCREENSHOT_ROOTS:
            if p.is_relative_to(root.resolve()):
                p.parent.mkdir(parents=True, exist_ok=True)
                return p
    except Exception:
        pass
    return fallback

def _require_pyautogui():
    if not _PYAUTOGUI:
        raise RuntimeError("PyAutoGUI not installed. Run: pip install pyautogui")

_FIRST_NAMES = [
    "Alex", "Jordan", "Taylor", "Morgan", "Casey", "Riley", "Drew", "Quinn",
    "Avery", "Blake", "Cameron", "Dakota", "Emerson", "Finley", "Harper",
]
_LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller",
    "Davis", "Wilson", "Moore", "Taylor", "Anderson", "Thomas", "Jackson",
]
_DOMAINS = ["gmail.com", "yahoo.com", "outlook.com", "proton.me", "mail.com"]


def _random_data(data_type: str) -> str:
    dt = data_type.lower().strip()

    if dt == "first_name":
        return random.choice(_FIRST_NAMES)

    if dt == "last_name":
        return random.choice(_LAST_NAMES)

    if dt == "name":
        return f"{random.choice(_FIRST_NAMES)} {random.choice(_LAST_NAMES)}"

    if dt == "email":
        first = random.choice(_FIRST_NAMES).lower()
        last  = random.choice(_LAST_NAMES).lower()
        num   = random.randint(10, 999)
        return f"{first}.{last}{num}@{random.choice(_DOMAINS)}"

    if dt == "username":
        return f"{random.choice(_FIRST_NAMES).lower()}{random.randint(100, 9999)}"

    if dt == "password":
        chars = string.ascii_letters + string.digits + "!@#$%"
        raw   = (
            random.choice(string.ascii_uppercase)
            + random.choice(string.digits)
            + random.choice("!@#$%")
            + "".join(random.choices(chars, k=9))
        )
        return "".join(random.sample(raw, len(raw)))

    if dt == "phone":
        return f"+1{random.randint(200,999)}{random.randint(1_000_000, 9_999_999)}"

    if dt == "birthday":
        y = random.randint(1980, 2000)
        m = random.randint(1, 12)
        d = random.randint(1, 28)
        return f"{m:02d}/{d:02d}/{y}"

    if dt == "address":
        num    = random.randint(100, 9999)
        street = random.choice(["Main St", "Oak Ave", "Park Blvd", "Elm St", "Cedar Ln"])
        return f"{num} {street}"

    if dt == "zip_code":
        return str(random.randint(10000, 99999))

    if dt == "city":
        return random.choice(["New York", "Los Angeles", "Chicago", "Houston", "Phoenix"])

    return f"random_{data_type}_{random.randint(1000, 9999)}"

def _user_profile() -> dict:
    """Read identity fields from long-term memory."""
    try:
        if _MEMORY_PATH.exists():
            data     = json.loads(_MEMORY_PATH.read_text(encoding="utf-8"))
            identity = data.get("identity", {})
            return {k: v.get("value", "") for k, v in identity.items()}
    except Exception:
        pass
    return {}

def _type(text: str, interval: float = 0.03) -> str:
    _require_pyautogui()
    time.sleep(0.3)
    pyautogui.typewrite(text, interval=interval)
    return f"Typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _smart_type(text: str, clear_first: bool = True) -> str:
    _require_pyautogui()
    if clear_first:
        _clear_field()
        time.sleep(0.1)

    if len(text) > 20 and _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("ctrl", "v")
        return f"Smart-typed (clipboard): {text[:60]}{'…' if len(text) > 60 else ''}"

    pyautogui.typewrite(text, interval=0.04)
    return f"Smart-typed: {text[:60]}{'…' if len(text) > 60 else ''}"


def _click(x=None, y=None, button: str = "left", clicks: int = 1) -> str:
    _require_pyautogui()
    if x is not None and y is not None:
        pyautogui.click(x, y, button=button, clicks=clicks)
        return f"{'Double-c' if clicks == 2 else 'C'}licked ({x}, {y}) [{button}]"
    pyautogui.click(button=button, clicks=clicks)
    return f"Clicked at current position [{button}]"


def _hotkey(*keys) -> str:
    _require_pyautogui()
    pyautogui.hotkey(*keys)
    return f"Hotkey: {'+'.join(keys)}"


def _press(key: str) -> str:
    _require_pyautogui()
    pyautogui.press(key)
    return f"Pressed: {key}"


def _scroll(direction: str = "down", amount: int = 3) -> str:
    _require_pyautogui()
    vertical   = direction in ("up", "down")
    clicks     = amount if direction in ("up", "right") else -amount
    pyautogui.scroll(clicks) if vertical else pyautogui.hscroll(clicks)
    return f"Scrolled {direction} ×{amount}"


def _move(x: int, y: int, duration: float = 0.3) -> str:
    _require_pyautogui()
    pyautogui.moveTo(x, y, duration=duration)
    return f"Mouse → ({x}, {y})"


def _drag(x1: int, y1: int, x2: int, y2: int, duration: float = 0.5) -> str:
    _require_pyautogui()
    pyautogui.moveTo(x1, y1, duration=0.2)
    pyautogui.dragTo(x2, y2, duration=duration, button="left")
    return f"Dragged ({x1},{y1}) → ({x2},{y2})"


def _clipboard_get() -> str:
    if _PYPERCLIP:
        return pyperclip.paste()
    _hotkey("ctrl", "c")
    time.sleep(0.2)
    return "(copied — pyperclip unavailable for read)"


def _clipboard_paste(text: str) -> str:
    if _PYPERCLIP:
        pyperclip.copy(text)
        time.sleep(0.1)
        _require_pyautogui()
        pyautogui.hotkey("ctrl", "v")
        return f"Pasted: {text[:60]}{'…' if len(text) > 60 else ''}"
    return "pyperclip not available"


def _screenshot(save_path: str | None = None) -> str:
    _require_pyautogui()
    path = _safe_screenshot_path(save_path)
    img  = pyautogui.screenshot()
    img.save(str(path))
    return f"Screenshot saved: {path}"


def _clear_field() -> str:
    _require_pyautogui()
    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.1)
    pyautogui.press("delete")
    return "Field cleared"

def _focus_window(title: str) -> str:
    os_name = _get_os()

    if os_name == "windows":
        try:
            script = f'(New-Object -ComObject WScript.Shell).AppActivate("{title}")'
            subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (Windows) failed: {e}"

    if os_name == "mac":
        script = (
            f'tell application "System Events" to '
            f'set frontmost of (first process whose name contains "{title}") to true'
        )
        try:
            subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except Exception as e:
            return f"focus_window (macOS) failed: {e}"

    if os_name == "linux":
        try:
            result = subprocess.run(
                ["wmctrl", "-a", title],
                capture_output=True, timeout=5,
            )
            if result.returncode == 0:
                time.sleep(0.3)
                return f"Focused window: {title}"
        except FileNotFoundError:
            pass
        try:
            result = subprocess.run(
                ["xdotool", "search", "--name", title, "windowactivate"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.3)
            return f"Focused window: {title}"
        except FileNotFoundError:
            return "focus_window (Linux) requires wmctrl or xdotool"
        except Exception as e:
            return f"focus_window (Linux) failed: {e}"

    return f"focus_window: unknown OS '{os_name}'"
def _screen_find(description: str) -> tuple[int, int] | None:
    try:
        import base64
        from or_client import client

        _require_pyautogui()
        w, h  = pyautogui.size()
        img   = pyautogui.screenshot()
        buf   = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()

        text = client.vision(
            f"This is a screenshot of a {w}×{h} pixel screen. "
            f"Locate the UI element: '{description}'. "
            f"Reply ONLY with center coordinates as: x,y — or NOT_FOUND",
            image_b64=b64,
            mime="image/png",
        )

        if "NOT_FOUND" in text.upper():
            return None
        match = re.search(r"(\d+)\s*,\s*(\d+)", text)
        if match:
            return int(match.group(1)), int(match.group(2))
    except Exception as e:
        print(f"[ComputerControl] ⚠️ screen_find failed: {e}")
    return None


# ─── UI Automation (find & click elements by name) ──────────────────

def _ui_find_element(name: str, element_type: str = "any", index: int = 0) -> dict | None:
    """Find a UI element by name using Windows UI Automation (COM).
    Returns dict with name, type, x, y, width, height or None."""

    os_name = _get_os()

    if os_name == "windows":
        return _ui_find_windows(name, element_type, index)

    if os_name == "mac":
        return _ui_find_mac(name, element_type, index)

    if os_name == "linux":
        return _ui_find_linux(name, element_type, index)

    return None


def _ui_find_windows(name: str, element_type: str, index: int) -> dict | None:
    """Find UI element on Windows using UI Automation via PowerShell."""
    try:
        # Map element types to UI Automation control types
        type_map = {
            "button": "Button",
            "btn": "Button",
            "link": "Hyperlink",
            "text": "Text",
            "input": "Edit",
            "field": "Edit",
            "edit": "Edit",
            "checkbox": "CheckBox",
            "radio": "RadioButton",
            "tab": "TabItem",
            "menu": "MenuItem",
            "tree": "TreeItem",
            "list": "ListItem",
            "combo": "ComboBox",
            "dropdown": "ComboBox",
            "any": "",
        }
        control_type = type_map.get(element_type.lower(), "")

        # PowerShell script using UI Automation
        ps_script = '''
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
Add-Type -AssemblyName System.Windows.Forms

$root = [System.Windows.Automation.AutomationElement]::RootElement
$cond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, "%s"
)
$items = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)

if ($items.Count -eq 0) {
    # Try partial match with Name containing the text
    $cond2 = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::NameProperty, "%s",
        [System.Windows.Automation.Condition]::True
    )
    $items = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond2)
}

if ($items.Count -eq 0) {
    # Try AutomationId match
    $cond3 = New-Object System.Windows.Automation.PropertyCondition(
        [System.Windows.Automation.AutomationElement]::AutomationIdProperty, "%s"
    )
    $items = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond3)
}

$idx = %d
if ($items.Count -gt $idx) {
    $item = $items[$idx]
    $rect = $item.Current.BoundingRectangle
    $cx = [int](($rect.Left + $rect.Right) / 2)
    $cy = [int](($rect.Top + $rect.Bottom) / 2)
    $w = [int]($rect.Right - $rect.Left)
    $h = [int]($rect.Bottom - $rect.Top)
    Write-Output "FOUND"
    Write-Output $item.Current.Name
    Write-Output $item.Current.ControlType.ProgrammaticName
    Write-Output "$cx"
    Write-Output "$cy"
    Write-Output "$w"
    Write-Output "$h"
} else {
    Write-Output "NOT_FOUND"
    Write-Output $items.Count
}
''' % (name, name, name, index)

        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=15
        )

        lines = result.stdout.strip().splitlines()
        if lines and lines[0] == "FOUND" and len(lines) >= 7:
            return {
                "name": lines[1],
                "type": lines[2].replace("ControlType.", ""),
                "x": int(lines[3]),
                "y": int(lines[4]),
                "width": int(lines[5]),
                "height": int(lines[6]),
            }

    except subprocess.TimeoutExpired:
        print("[ComputerControl] UI find timed out")
    except Exception as e:
        print(f"[ComputerControl] UI find (Windows) error: {e}")

    return None


def _ui_find_mac(name: str, element_type: str, index: int) -> dict | None:
    """Find UI element on macOS using AppleScript/Accessibility."""
    try:
        script = f'''
tell application "System Events"
    set frontApp to first process whose frontmost is true
    set allItems to every UI element of frontApp whose name contains "{name}"
    if (count of allItems) > {index} then
        set item to item {index + 1} of allItems
        set pos to position of item
        set sz to size of item
        return "FOUND\\n" & (name of item) & "\\n" & ((item 1 of pos) as text) & "\\n" & ((item 2 of pos) as text) & "\\n" & ((item 1 of sz) as text) & "\\n" & ((item 2 of sz) as text)
    end if
end tell
return "NOT_FOUND"
'''
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=10
        )
        lines = result.stdout.strip().splitlines()
        if lines and lines[0] == "FOUND" and len(lines) >= 6:
            x = int(lines[2])
            y = int(lines[3])
            w = int(lines[4])
            h = int(lines[5])
            return {
                "name": lines[1],
                "type": element_type,
                "x": x + w // 2,
                "y": y + h // 2,
                "width": w,
                "height": h,
            }
    except Exception as e:
        print(f"[ComputerControl] UI find (macOS) error: {e}")
    return None


def _ui_find_linux(name: str, element_type: str, index: int) -> dict | None:
    """Find UI element on Linux using xdotool/wmctrl."""
    try:
        # Use xdotool to search for windows
        result = subprocess.run(
            ["xdotool", "search", "--name", name],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            # Get window geometry
            wid = result.stdout.strip().splitlines()[0]
            geo = subprocess.run(
                ["xdotool", "getwindowgeometry", "--shell", wid],
                capture_output=True, text=True, timeout=5
            )
            info = {}
            for line in geo.stdout.strip().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    info[k] = int(v)
            if "X" in info and "Y" in info:
                return {
                    "name": name,
                    "type": "window",
                    "x": info["X"] + info.get("WIDTH", 0) // 2,
                    "y": info["Y"] + info.get("HEIGHT", 0) // 2,
                    "width": info.get("WIDTH", 0),
                    "height": info.get("HEIGHT", 0),
                }
    except Exception as e:
        print(f"[ComputerControl] UI find (Linux) error: {e}")
    return None


def _ui_click_element(name: str, element_type: str = "any", click_type: str = "left", index: int = 0) -> str:
    """Find a UI element by name and click on it."""
    elem = _ui_find_element(name, element_type, index)
    if not elem:
        # Fallback to AI vision
        coords = _screen_find(name)
        if coords:
            time.sleep(0.2)
            clicks = 2 if click_type == "double" else 1
            btn = "right" if click_type == "right" else "left"
            _click(x=coords[0], y=coords[1], button=btn, clicks=clicks)
            return f"Clicked '{name}' at {coords} (AI vision fallback)"
        return f"Element not found: '{name}'"

    x, y = elem["x"], elem["y"]
    clicks = 2 if click_type == "double" else 1
    btn = "right" if click_type == "right" else "left"

    time.sleep(0.2)
    _click(x=x, y=y, button=btn, clicks=clicks)
    return f"Clicked '{elem['name']}' ({elem['type']}) at ({x}, {y})"


def _ui_type_in_field(name: str, text: str, clear_first: bool = True, index: int = 0) -> str:
    """Find a text field by name, click it, and type text into it."""
    # Try to find as Edit/input field first
    elem = _ui_find_element(name, "edit", index)
    if not elem:
        # Try any element with that name
        elem = _ui_find_element(name, "any", index)

    if not elem:
        # Fallback to AI vision
        coords = _screen_find(name)
        if coords:
            time.sleep(0.2)
            _click(x=coords[0], y=coords[1])
            time.sleep(0.3)
            if clear_first:
                _clear_field()
                time.sleep(0.1)
            return _smart_type(text, clear_first=False)
        return f"Field not found: '{name}'"

    x, y = elem["x"], elem["y"]
    _click(x=x, y=y)
    time.sleep(0.3)

    if clear_first:
        _clear_field()
        time.sleep(0.1)

    return _smart_type(text, clear_first=False)


def _ui_list_elements(element_type: str = "any", filter_text: str = "") -> str:
    """List visible UI elements on screen (for discovery/debugging)."""
    os_name = _get_os()

    if os_name != "windows":
        return "UI element listing is only supported on Windows currently."

    try:
        type_filter = ""
        if element_type.lower() != "any":
            type_map = {
                "button": "Button", "btn": "Button", "link": "Hyperlink",
                "input": "Edit", "field": "Edit", "edit": "Edit",
                "checkbox": "CheckBox", "radio": "RadioButton",
                "tab": "TabItem", "menu": "MenuItem", "list": "ListItem",
                "combo": "ComboBox", "dropdown": "ComboBox",
            }
            ct = type_map.get(element_type.lower(), element_type)
            type_filter = f'''
$ctCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
    [System.Windows.Automation.ControlType]::{ct}
)
$cond = New-Object System.Windows.Automation.AndCondition($nameCond, $ctCond)
'''
        else:
            type_filter = "$cond = $nameCond"

        name_filter = f'''
$nameCond = New-Object System.Windows.Automation.PropertyCondition(
    [System.Windows.Automation.AutomationElement]::NameProperty, "{filter_text}",
    [System.Windows.Automation.Condition]::True
)
''' if filter_text else '''
$nameCond = [System.Windows.Automation.Condition]::TrueCondition
'''

        ps_script = f'''
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$root = [System.Windows.Automation.AutomationElement]::RootElement
{name_filter}
{type_filter}
$items = $root.FindAll([System.Windows.Automation.TreeScope]::Descendants, $cond)

$count = [Math]::Min($items.Count, 30)
for ($i = 0; $i -lt $count; $i++) {{
    $item = $items[$i]
    $rect = $item.Current.BoundingRectangle
    $cx = [int](($rect.Left + $rect.Right) / 2)
    $cy = [int](($rect.Top + $rect.Bottom) / 2)
    $nm = $item.Current.Name
    $ct = $item.Current.ControlType.ProgrammaticName.Replace("ControlType.", "")
    if ($nm -and $rect.Right - $rect.Left -gt 0 -and $rect.Bottom - $rect.Top -gt 0) {{
        Write-Output "$i|$nm|$ct|$cx|$cy"
    }}
}}
Write-Output "TOTAL:$($items.Count)"
'''

        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=15
        )

        elements = []
        total = 0
        for line in result.stdout.strip().splitlines():
            if line.startswith("TOTAL:"):
                total = int(line.split(":")[1])
                continue
            parts = line.split("|")
            if len(parts) >= 5:
                elements.append({
                    "index": parts[0],
                    "name": parts[1],
                    "type": parts[2],
                    "x": parts[3],
                    "y": parts[4],
                })

        if not elements:
            return "No interactive elements found on screen."

        lines = [f"Found {total} elements (showing {len(elements)}):"]
        for e in elements:
            lines.append(f"  [{e['index']}] {e['type']:12s}  '{e['name']}'  at ({e['x']}, {e['y']})")

        return "\n".join(lines)

    except subprocess.TimeoutExpired:
        return "UI element listing timed out."
    except Exception as e:
        return f"UI element listing error: {e}"

def computer_control(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for all computer control actions.

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      text          : text to type or paste
      x, y          : screen coordinates
      button        : 'left' | 'right' (default: left)
      keys          : hotkey string, e.g. 'ctrl+c'
      key           : single key name, e.g. 'enter'
      direction     : 'up' | 'down' | 'left' | 'right'
      amount        : scroll amount (default: 3)
      seconds       : wait duration
      title         : window title fragment for focus_window
      description   : natural-language element description for screen_find/click
      type          : data type for random_data
      field         : memory field name for user_data
      clear_first   : bool, clear field before typing (default: true)
      path          : save path for screenshot (must be inside home dir)

    Actions:
      type          — type text at cursor
      smart_type    — clear field + type (clipboard-backed)
      click         — left click
      double_click  — double left click
      right_click   — right click
      move          — move mouse
      drag          — click-drag between two points
      hotkey        — key combination
      press         — single key
      scroll        — scroll the wheel
      copy          — read clipboard
      paste         — write + paste clipboard
      screenshot    — capture screen (safe path only)
      wait          — sleep N seconds
      clear_field   — select-all + delete
      focus_window  — bring window to foreground
      screen_find   — AI element finder (returns x,y)
      screen_click  — AI element finder + click
      ui_click      — Find & click UI element by name (button, link, etc.)
      ui_type       — Find text field by name & type into it
      ui_find       — Find UI element, return its position
      ui_list       — List visible UI elements on screen
      random_data   — generate fake form data
      user_data     — pull real data from memory
    """
    params = parameters or {}
    action = params.get("action", "").lower().strip()

    if not action:
        return "No action specified for computer_control."

    if player:
        player.write_log(f"[Computer] {action}")

    print(f"[ComputerControl] ▶ {action}  {params}")

    try:

        if action == "type":
            return _type(params.get("text", ""))

        if action == "smart_type":
            return _smart_type(
                params.get("text", ""),
                clear_first=params.get("clear_first", True),
            )

        if action in ("click", "left_click"):
            return _click(params.get("x"), params.get("y"), "left", 1)

        if action == "double_click":
            return _click(params.get("x"), params.get("y"), "left", 2)

        if action == "right_click":
            return _click(params.get("x"), params.get("y"), "right", 1)

        if action == "move":
            return _move(int(params.get("x", 0)), int(params.get("y", 0)))

        if action == "drag":
            return _drag(
                int(params.get("x1", 0)), int(params.get("y1", 0)),
                int(params.get("x2", 0)), int(params.get("y2", 0)),
            )

        if action == "hotkey":
            raw  = params.get("keys", "")
            keys = [k.strip() for k in raw.split("+")] if isinstance(raw, str) else raw
            return _hotkey(*keys)

        if action == "press":
            return _press(params.get("key", "enter"))

        if action == "scroll":
            return _scroll(
                direction=params.get("direction", "down"),
                amount=int(params.get("amount", 3)),
            )

        if action == "copy":
            return _clipboard_get()

        if action == "paste":
            return _clipboard_paste(params.get("text", ""))

        if action == "screenshot":
            return _screenshot(params.get("path"))

        if action == "screen_find":
            coords = _screen_find(params.get("description", ""))
            return f"{coords[0]},{coords[1]}" if coords else "NOT_FOUND"

        if action == "screen_click":
            desc   = params.get("description", "")
            coords = _screen_find(desc)
            if coords:
                time.sleep(0.2)
                _click(x=coords[0], y=coords[1])
                return f"Clicked '{desc}' at {coords}"
            return f"Element not found on screen: '{desc}'"

        if action == "ui_click":
            return _ui_click_element(
                name=params.get("name", params.get("description", "")),
                element_type=params.get("element_type", "any"),
                click_type=params.get("click_type", "left"),
                index=int(params.get("index", 0)),
            )

        if action == "ui_type":
            return _ui_type_in_field(
                name=params.get("name", params.get("description", "")),
                text=params.get("text", ""),
                clear_first=params.get("clear_first", True),
                index=int(params.get("index", 0)),
            )

        if action == "ui_find":
            elem = _ui_find_element(
                name=params.get("name", params.get("description", "")),
                element_type=params.get("element_type", "any"),
                index=int(params.get("index", 0)),
            )
            if elem:
                return f"Found '{elem['name']}' ({elem['type']}) at ({elem['x']}, {elem['y']}) size {elem['width']}x{elem['height']}"
            return f"Element not found: '{params.get('name', '')}'"

        if action == "ui_list":
            return _ui_list_elements(
                element_type=params.get("element_type", "any"),
                filter_text=params.get("filter", ""),
            )

        if action == "wait":
            secs = float(params.get("seconds", 1.0))
            secs = min(secs, 30.0)
            time.sleep(secs)
            return f"Waited {secs}s"

        if action == "clear_field":
            return _clear_field()

        if action == "focus_window":
            return _focus_window(params.get("title", ""))

        if action == "random_data":
            dt     = params.get("type", "name")
            result = _random_data(dt)
            print(f"[ComputerControl] 🎲 random {dt} → {result}")
            return result

        if action == "user_data":
            field   = params.get("field", "name")
            profile = _user_profile()
            value   = profile.get(field, "")
            if not value:
                value = _random_data(field)
                print(f"[ComputerControl] ⚠️ No '{field}' in memory, using random: {value}")
            return value

        return f"Unknown action: '{action}'"

    except Exception as e:
        print(f"[ComputerControl] ❌ {action}: {e}")
        return f"computer_control '{action}' failed: {e}"
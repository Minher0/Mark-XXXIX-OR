# actions/open_app.py
# MARK XXV — Cross-Platform App Launcher

import time
import subprocess
import platform
import shutil
import sys
import os
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_APP_ALIASES = {
    "whatsapp":           {"Windows": "WhatsApp",               "Darwin": "WhatsApp",            "Linux": "whatsapp"},
    "chrome":             {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                 "Darwin": "Google Chrome",       "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                "Darwin": "Firefox",             "Linux": "firefox"},
    "spotify":            {"Windows": "Spotify",                "Darwin": "Spotify",             "Linux": "spotify"},
    "vscode":             {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "visual studio code": {"Windows": "code",                   "Darwin": "Visual Studio Code",  "Linux": "code"},
    "discord":            {"Windows": "Discord",                "Darwin": "Discord",             "Linux": "discord"},
    "telegram":           {"Windows": "Telegram",               "Darwin": "Telegram",            "Linux": "telegram"},
    "instagram":          {"Windows": "Instagram",              "Darwin": "Instagram",           "Linux": "instagram"},
    "tiktok":             {"Windows": "TikTok",                 "Darwin": "TikTok",              "Linux": "tiktok"},
    "notepad":            {"Windows": "notepad.exe",            "Darwin": "TextEdit",            "Linux": "gedit"},
    "calculator":         {"Windows": "calc.exe",               "Darwin": "Calculator",          "Linux": "gnome-calculator"},
    "terminal":           {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "gnome-terminal"},
    "cmd":                {"Windows": "cmd.exe",                "Darwin": "Terminal",            "Linux": "bash"},
    "explorer":           {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",           "Darwin": "Finder",              "Linux": "nautilus"},
    "paint":              {"Windows": "mspaint.exe",            "Darwin": "Preview",             "Linux": "gimp"},
    "word":               {"Windows": "winword",                "Darwin": "Microsoft Word",      "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                  "Darwin": "Microsoft Excel",     "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",               "Darwin": "Microsoft PowerPoint","Linux": "libreoffice --impress"},
    "vlc":                {"Windows": "vlc",                    "Darwin": "VLC",                 "Linux": "vlc"},
    "zoom":               {"Windows": "Zoom",                   "Darwin": "zoom.us",             "Linux": "zoom"},
    "slack":              {"Windows": "Slack",                  "Darwin": "Slack",               "Linux": "slack"},
    "steam":              {"Windows": "steam",                  "Darwin": "Steam",               "Linux": "steam"},
    "task manager":       {"Windows": "taskmgr.exe",            "Darwin": "Activity Monitor",    "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",           "Darwin": "System Preferences",  "Linux": "gnome-control-center"},
    "powershell":         {"Windows": "powershell.exe",         "Darwin": "Terminal",            "Linux": "bash"},
    "edge":               {"Windows": "msedge",                 "Darwin": "Microsoft Edge",      "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                  "Darwin": "Brave Browser",       "Linux": "brave-browser"},
    "obsidian":           {"Windows": "Obsidian",               "Darwin": "Obsidian",            "Linux": "obsidian"},
    "notion":             {"Windows": "Notion",                 "Darwin": "Notion",              "Linux": "notion"},
    "blender":            {"Windows": "blender",                "Darwin": "Blender",             "Linux": "blender"},
    "capcut":             {"Windows": "CapCut",                 "Darwin": "CapCut",              "Linux": "capcut"},
    "postman":            {"Windows": "Postman",                "Darwin": "Postman",             "Linux": "postman"},
    "figma":              {"Windows": "Figma",                  "Darwin": "Figma",               "Linux": "figma"},
}


def _normalize(raw: str) -> str:
    system = platform.system()
    key    = raw.lower().strip()
    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(system, raw)
    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(system, raw)
    return raw


def _is_running(app_name: str) -> bool:
    if not _PSUTIL:
        return True
    app_lower = app_name.lower().replace(" ", "").replace(".exe", "")
    try:
        for proc in psutil.process_iter(["name"]):
            try:
                proc_name = proc.info["name"].lower().replace(" ", "").replace(".exe", "")
                if app_lower in proc_name or proc_name in app_lower:
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        pass
    return False


_WINDOWS_APP_PATHS = {
    "chrome":     r"Google\Chrome\Application\chrome.exe",
    "firefox":    r"Mozilla Firefox\firefox.exe",
    "spotify":    r"Spotify\Spotify.exe",
    "vscode":     r"Microsoft VS Code\Code.exe",
    "discord":    r"Discord\Discord.exe",
    "telegram":   r"Telegram Desktop\Telegram.exe",
    "whatsapp":   r"WhatsApp\WhatsApp.exe",
    "notepad":    r"Windows NT\Accessories\wordpad.exe",
    "brave":      r"BraveSoftware\Brave-Browser\Application\brave.exe",
    "edge":       r"Microsoft\Edge\Application\msedge.exe",
}


def _find_windows_exe(app_name: str) -> str | None:
    key = app_name.lower().replace(".exe", "").strip()
    rel_path = _WINDOWS_APP_PATHS.get(key)
    if not rel_path:
        for k, v in _WINDOWS_APP_PATHS.items():
            if k in key or key in k:
                rel_path = v
                break
    if not rel_path:
        return None
    candidates = [
        Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / rel_path,
        Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / rel_path,
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / rel_path,
        Path.home() / "AppData" / "Local" / rel_path,
    ]
    for p in candidates:
        if p.exists():
            return str(p)
    return None


def _launch_windows(app_name: str) -> bool:
    """Launch app on Windows using Win key + type + Enter as primary method."""
    # ── PRIMARY: Win key + type + Enter (same as user doing it manually) ──
    # This works for ALL apps — Start menu search finds everything.
    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(2.5)
        print(f"[open_app] ✅ Win+type: {app_name}")
        return True
    except Exception as e:
        print(f"[open_app] ⚠️ Win+type failed: {e}")

    # ── FALLBACK 1: CMD start command ──
    try:
        cmd_str = f'start "" "{app_name}"'
        subprocess.Popen(
            cmd_str,
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(1.5)
        print(f"[open_app] ✅ CMD start: {app_name}")
        return True
    except Exception as e:
        print(f"[open_app] ⚠️ Windows CMD start failed: {e}")

    # ── FALLBACK 2: Direct exe path ──
    try:
        exe = _find_windows_exe(app_name)
        if exe:
            subprocess.Popen(
                [exe],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            print(f"[open_app] ✅ Direct exe: {exe}")
            return True
    except Exception as e:
        print(f"[open_app] ⚠️ Windows direct exe launch failed: {e}")

    return False

def _launch_macos(app_name: str) -> bool:
    try:
        result = subprocess.run(["open", "-a", app_name], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(["open", "-a", f"{app_name}.app"], capture_output=True, timeout=8)
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] ⚠️ macOS Spotlight failed: {e}")
        return False



def _launch_linux(app_name: str) -> bool:
    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-"))
    )
    if binary:
        try:
            subprocess.Popen([binary], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(["xdg-open", app_name], capture_output=True, timeout=5)
        return True
    except Exception:
        pass

    try:
        desktop_name = app_name.lower().replace(" ", "-")
        subprocess.run(["gtk-launch", desktop_name], capture_output=True, timeout=5)
        return True
    except Exception:
        pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}


def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    app_name = (parameters or {}).get("app_name", "").strip()

    if not app_name:
        return "Please specify which application to open, sir."

    app_lower = app_name.lower()
    memory_keywords = ["memory", "mémoire", "memoire", "long term", "long-term", "longterm", "long terme", "long-terme"]
    if any(kw in app_lower for kw in memory_keywords):
        base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        memory_file = base / "memory" / "long_term.json"
        if not memory_file.exists():
            return "Memory file not found: memory/long_term.json"
        try:
            if platform.system() == "Windows":
                cmd_str = f'start "" "{memory_file}"'
                subprocess.Popen(
                    cmd_str,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            elif platform.system() == "Darwin":
                subprocess.Popen(["open", str(memory_file)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                subprocess.Popen(["xdg-open", str(memory_file)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)
            print(f"[open_app] 🧠 Opened memory file via CMD: {memory_file}")
            if player:
                player.write_log("[open_app] memory/long_term.json")
            return "Opened long-term memory file, sir."
        except Exception as e:
            return f"Failed to open memory file: {e}"

    system   = platform.system()
    launcher = _OS_LAUNCHERS.get(system)

    if launcher is None:
        return f"Unsupported OS: {system}"

    normalized = _normalize(app_name)
    print(f"[open_app] 🚀 Launching: {app_name} → {normalized} ({system})")

    if player:
        player.write_log(f"[open_app] {app_name}")

    try:
        success = launcher(normalized)

        if success:
            return f"Opened {app_name} successfully, sir."

        if normalized != app_name:
            success = launcher(app_name)
            if success:
                return f"Opened {app_name} successfully, sir."

        return (
            f"I tried to open {app_name}, sir, but couldn't confirm it launched. "
            f"It may still be loading or might not be installed."
        )

    except Exception as e:
        print(f"[open_app] ❌ {e}")
        return f"Failed to open {app_name}, sir: {e}"


# ─── Close app ──────────────────────────────────────────────

# Process names to NEVER kill (would crash Jarvis or the system)
_PROTECTED_PROCESSES = {
    "python", "pythonw", "python.exe", "python3",
    "main.py", "jarvis",
    "explorer", "explorer.exe",
    "csrss", "csrss.exe", "lsass", "lsass.exe",
    "services", "services.exe", "svchost", "svchost.exe",
    "wininit", "wininit.exe", "winlogon", "winlogon.exe",
    "dwm", "dwm.exe", "taskmgr", "taskmgr.exe",
}

# App name → process name mapping
_APP_PROCESS_MAP = {
    "chrome":        ["chrome.exe", "chrome"],
    "google chrome": ["chrome.exe", "chrome"],
    "firefox":       ["firefox.exe", "firefox"],
    "spotify":       ["Spotify.exe", "Spotify", "spotify"],
    "discord":       ["Discord.exe", "Discord", "discord"],
    "telegram":      ["Telegram.exe", "Telegram", "telegram-desktop"],
    "whatsapp":      ["WhatsApp.exe", "WhatsApp"],
    "vscode":        ["Code.exe", "Code", "code"],
    "visual studio code": ["Code.exe", "Code", "code"],
    "edge":          ["msedge.exe", "msedge"],
    "brave":         ["brave.exe", "brave"],
    "vlc":           ["vlc.exe", "vlc"],
    "zoom":          ["Zoom.exe", "Zoom", "zoom"],
    "slack":         ["Slack.exe", "Slack", "slack"],
    "steam":         ["steam.exe", "steam"],
    "notion":        ["Notion.exe", "Notion"],
    "obsidian":      ["Obsidian.exe", "Obsidian"],
    "word":          ["WINWORD.EXE", "WINWORD"],
    "excel":         ["EXCEL.EXE", "EXCEL"],
    "powerpoint":    ["POWERPNT.EXE", "POWERPNT"],
    "paint":         ["mspaint.exe", "mspaint"],
    "notepad":       ["notepad.exe", "notepad"],
    "calculator":    ["Calculator.exe", "Calculator", "calc.exe"],
    "capcut":        ["CapCut.exe", "CapCut"],
    "figma":         ["Figma.exe", "Figma"],
    "postman":       ["Postman.exe", "Postman"],
    "blender":       ["blender.exe", "blender"],
    "obs":           ["obs64.exe", "obs"],
    "twitch":        ["Twitch.exe", "Twitch"],
    "epic games":    ["EpicGamesLauncher.exe", "EpicGamesLauncher"],
    "spotify":       ["Spotify.exe", "Spotify"],
    "outlook":       ["OUTLOOK.EXE", "OUTLOOK"],
    "teams":         ["ms-teams.exe", "ms-teams", "Teams.exe", "Teams"],
}


def _find_process_names(app_name: str) -> list:
    """Get likely process names for an app."""
    key = app_name.lower().replace(".exe", "").strip()
    if key in _APP_PROCESS_MAP:
        return _APP_PROCESS_MAP[key]
    # Fuzzy match
    for mapped_key, procs in _APP_PROCESS_MAP.items():
        if key in mapped_key or mapped_key in key:
            return procs
    # Generate likely process name
    base = app_name.strip()
    if platform.system() == "Windows":
        return [base, f"{base}.exe"]
    return [base.lower()]


def close_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Close an application by name — uses taskkill (Windows) / pkill (Mac/Linux).
    Does NOT use Alt+F4, so it won't accidentally close the focused window."""
    app_name = (parameters or {}).get("app_name", "").strip()
    if not app_name:
        return "Please specify which application to close."

    # Protect Jarvis itself
    if app_name.lower().replace(".exe", "") in _PROTECTED_PROCESSES:
        return f"I can't close '{app_name}' — that would shut me down!"

    proc_names = _find_process_names(app_name)
    system = platform.system()

    if player:
        player.write_log(f"[close_app] {app_name}")

    print(f"[close_app] 🛑 Closing: {app_name} (candidates: {proc_names})")

    if system == "Windows":
        # Use taskkill — /F = force, /IM = image name
        for proc in proc_names:
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/IM", proc],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    print(f"[close_app] ✅ Killed: {proc}")
                    return f"Closed {app_name}."
            except Exception:
                continue
        # Fallback: try with pyautogui (only if taskkill failed for all candidates)
        # Actually, don't use Alt+F4 at all — it closes the wrong window
        return f"Could not find a running process for '{app_name}'. It might not be open."

    elif system == "Darwin":
        # macOS: osascript or pkill
        for proc in proc_names:
            try:
                # Try AppleScript first (cleaner quit)
                result = subprocess.run(
                    ["osascript", "-e", f'tell application "{proc}" to quit'],
                    capture_output=True, text=True, timeout=5
                )
                if result.returncode == 0:
                    return f"Closed {app_name}."
            except Exception:
                pass
        # pkill fallback
        for proc in proc_names:
            try:
                result = subprocess.run(["pkill", "-f", proc], capture_output=True, timeout=5)
                if result.returncode == 0:
                    return f"Closed {app_name}."
            except Exception:
                continue
        return f"Could not find a running process for '{app_name}'."

    else:
        # Linux: pkill
        for proc in proc_names:
            try:
                result = subprocess.run(["pkill", "-f", proc], capture_output=True, timeout=5)
                if result.returncode == 0:
                    return f"Closed {app_name}."
            except Exception:
                continue
        return f"Could not find a running process for '{app_name}'."


# ─── List open apps ─────────────────────────────────────────

def list_open_apps(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """List currently running applications."""
    if not _PSUTIL:
        return "psutil is not installed. Run: pip install psutil"

    try:
        apps = set()
        for proc in psutil.process_iter(["name"]):
            try:
                name = proc.info["name"]
                if name:
                    # Skip system processes and tiny utilities
                    lower = name.lower()
                    if lower in _PROTECTED_PROCESSES:
                        continue
                    # Skip very short/system-like names
                    base = name.replace(".exe", "")
                    if len(base) <= 3:
                        continue
                    # Skip common system/background processes
                    skip = {
                        "conhost", "cmd", "powershell", "runtimebroker",
                        "searchhost", "shellexperiencehost", "startmenuexperiencehost",
                        "taskbar", "widgets", "dwm", "fontdrvhost", "lsass",
                        "sihost", "ctfmon", "dllhost", "svchost", "services",
                        "wininit", "winlogon", "csrss", "smss", "msdtc",
                        "nvspcap", "wermgr", "werfault", "mrt", "wuauclt",
                        "backgroundtaskhost", "applicationframehost",
                        "searchindexer", "securesystem", "memorycompression",
                        "registry", "system", "idle", "interrupts",
                        "threadpool", "spoolsv", "audiodg",
                    }
                    if base.lower() in skip:
                        continue
                    apps.add(base)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

        sorted_apps = sorted(apps, key=str.lower)
        if not sorted_apps:
            return "No applications currently running."

        lines = []
        for i, app in enumerate(sorted_apps[:30], 1):
            lines.append(f"{i}. {app}")

        result = f"Applications ouvertes ({len(sorted_apps)}):\n" + "\n".join(lines)
        if len(sorted_apps) > 30:
            result += f"\n... et {len(sorted_apps) - 30} autres."

        if player:
            player.write_log("[list_open_apps] listed")

        return result

    except Exception as e:
        return f"Failed to list applications: {e}"

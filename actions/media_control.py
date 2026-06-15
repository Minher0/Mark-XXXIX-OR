# actions/media_control.py
# MARK XXV — System-Level Media Controls
# Controls any media playing on the system regardless of which app is focused.
# Uses Windows Media Keys / macOS AppleScript / Linux playerctl.

import subprocess
import platform
import sys
import time

_OS = platform.system()  # "Windows" | "Darwin" | "Linux"

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    _PYAUTOGUI = True
except ImportError:
    _PYAUTOGUI = False


# ─── Play / Pause ───────────────────────────────────────────

def _play_pause_windows() -> str:
    """Send system-wide play/pause media key."""
    try:
        import ctypes
        # VK_MEDIA_PLAY_PAUSE = 0xB3
        VK_MEDIA_PLAY_PAUSE = 0xB3
        KEYEVENTF_KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_MEDIA_PLAY_PAUSE, 0, KEYEVENTF_KEYUP, 0)
        return "Play/Pause toggled."
    except Exception as e:
        # Fallback to pyautogui
        if _PYAUTOGUI:
            pyautogui.press("playpause")
            return "Play/Pause toggled (pyautogui)."
        return f"Failed: {e}"


def _play_pause_macos() -> str:
    """macOS play/pause via AppleScript."""
    try:
        subprocess.run([
            "osascript", "-e",
            'tell application "System Events" to key code 16 using {command down, option down}'
        ], capture_output=True, timeout=5)
        # Alternative: just use the media key via HID
        subprocess.run([
            "osascript", "-e",
            'tell application "Spotify" to playpause'
        ], capture_output=True, timeout=5)
        return "Play/Pause toggled."
    except Exception:
        # Fallback: use osascript with Music
        try:
            subprocess.run([
                "osascript", "-e",
                'tell application "Music" to playpause'
            ], capture_output=True, timeout=5)
            return "Play/Pause toggled (Music)."
        except Exception as e:
            return f"Failed: {e}"


def _play_pause_linux() -> str:
    """Linux play/pause via playerctl or xdotool."""
    try:
        result = subprocess.run(
            ["playerctl", "play-pause"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return "Play/Pause toggled."
    except Exception:
        pass
    # Fallback: xdotool send XF86AudioPlay
    try:
        subprocess.run(
            ["xdotool", "key", "XF86AudioPlay"],
            capture_output=True, timeout=5
        )
        return "Play/Pause toggled (xdotool)."
    except Exception as e:
        return f"Failed: {e}"


# ─── Next Track ─────────────────────────────────────────────

def _next_track_windows() -> str:
    try:
        import ctypes
        VK_MEDIA_NEXT_TRACK = 0xB0
        KEYEVENTF_KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_MEDIA_NEXT_TRACK, 0, KEYEVENTF_KEYUP, 0)
        return "Next track."
    except Exception:
        if _PYAUTOGUI:
            pyautogui.press("nexttrack")
            return "Next track (pyautogui)."
        return "Failed to skip to next track."


def _next_track_macos() -> str:
    try:
        # Try Spotify first
        r = subprocess.run([
            "osascript", "-e",
            'tell application "Spotify" to next track'
        ], capture_output=True, timeout=5)
        if r.returncode == 0:
            return "Next track (Spotify)."
    except Exception:
        pass
    try:
        subprocess.run([
            "osascript", "-e",
            'tell application "Music" to next track'
        ], capture_output=True, timeout=5)
        return "Next track (Music)."
    except Exception as e:
        return f"Failed: {e}"


def _next_track_linux() -> str:
    try:
        result = subprocess.run(
            ["playerctl", "next"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return "Next track."
    except Exception:
        pass
    try:
        subprocess.run(
            ["xdotool", "key", "XF86AudioNext"],
            capture_output=True, timeout=5
        )
        return "Next track (xdotool)."
    except Exception as e:
        return f"Failed: {e}"


# ─── Previous Track ─────────────────────────────────────────

def _previous_track_windows() -> str:
    try:
        import ctypes
        VK_MEDIA_PREV_TRACK = 0xB1
        KEYEVENTF_KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_MEDIA_PREV_TRACK, 0, KEYEVENTF_KEYUP, 0)
        return "Previous track."
    except Exception:
        if _PYAUTOGUI:
            pyautogui.press("prevtrack")
            return "Previous track (pyautogui)."
        return "Failed to go to previous track."


def _previous_track_macos() -> str:
    try:
        r = subprocess.run([
            "osascript", "-e",
            'tell application "Spotify" to previous track'
        ], capture_output=True, timeout=5)
        if r.returncode == 0:
            return "Previous track (Spotify)."
    except Exception:
        pass
    try:
        subprocess.run([
            "osascript", "-e",
            'tell application "Music" to previous track'
        ], capture_output=True, timeout=5)
        return "Previous track (Music)."
    except Exception as e:
        return f"Failed: {e}"


def _previous_track_linux() -> str:
    try:
        result = subprocess.run(
            ["playerctl", "previous"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return "Previous track."
    except Exception:
        pass
    try:
        subprocess.run(
            ["xdotool", "key", "XF86AudioPrev"],
            capture_output=True, timeout=5
        )
        return "Previous track (xdotool)."
    except Exception as e:
        return f"Failed: {e}"


# ─── Stop ────────────────────────────────────────────────────

def _stop_windows() -> str:
    try:
        import ctypes
        VK_MEDIA_STOP = 0xB2
        KEYEVENTF_KEYUP = 0x0002
        ctypes.windll.user32.keybd_event(VK_MEDIA_STOP, 0, 0, 0)
        ctypes.windll.user32.keybd_event(VK_MEDIA_STOP, 0, KEYEVENTF_KEYUP, 0)
        return "Playback stopped."
    except Exception:
        if _PYAUTOGUI:
            pyautogui.press("stop")
            return "Playback stopped (pyautogui)."
        return "Failed to stop playback."


def _stop_macos() -> str:
    try:
        r = subprocess.run([
            "osascript", "-e",
            'tell application "Spotify" to pause'
        ], capture_output=True, timeout=5)
        if r.returncode == 0:
            return "Playback stopped (Spotify)."
    except Exception:
        pass
    try:
        subprocess.run([
            "osascript", "-e",
            'tell application "Music" to pause'
        ], capture_output=True, timeout=5)
        return "Playback stopped (Music)."
    except Exception as e:
        return f"Failed: {e}"


def _stop_linux() -> str:
    try:
        result = subprocess.run(
            ["playerctl", "stop"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return "Playback stopped."
    except Exception:
        pass
    try:
        subprocess.run(
            ["xdotool", "key", "XF86AudioStop"],
            capture_output=True, timeout=5
        )
        return "Playback stopped (xdotool)."
    except Exception as e:
        return f"Failed: {e}"


# ─── Seek (forward / backward) ──────────────────────────────

def _seek_windows(seconds: int) -> str:
    """Seek forward (positive) or backward (negative) by seconds on Windows.
    Uses the SystemMediaTransportControls API via PowerShell."""
    direction = "forward" if seconds > 0 else "backward"
    abs_secs = abs(seconds)

    # Try Spotify first via keyboard shortcut (Shift+Right/Left arrow)
    try:
        import pyautogui
        if seconds > 0:
            # Ctrl+Right in most media players seeks forward
            pyautogui.hotkey("ctrl", "right")
            return f"Seeked {direction} ~10 seconds (keyboard shortcut)."
        else:
            pyautogui.hotkey("ctrl", "left")
            return f"Seeked {direction} ~10 seconds (keyboard shortcut)."
    except Exception:
        pass

    return f"Seek {direction} {abs_secs}s not fully supported. Try using the app directly."


def _seek_macos(seconds: int) -> str:
    direction = "forward" if seconds > 0 else "backward"
    abs_secs = abs(seconds)

    # Try Spotify
    try:
        if seconds > 0:
            subprocess.run([
                "osascript", "-e",
                f'tell application "Spotify" to set player position to (player position + {abs_secs})'
            ], capture_output=True, timeout=5)
        else:
            subprocess.run([
                "osascript", "-e",
                f'tell application "Spotify" to set player position to (player position - {abs_secs})'
            ], capture_output=True, timeout=5)
        return f"Seeked {direction} {abs_secs} seconds (Spotify)."
    except Exception:
        pass

    # Try Apple Music
    try:
        if seconds > 0:
            subprocess.run([
                "osascript", "-e",
                f'tell application "Music" to set player position to (player position + {abs_secs})'
            ], capture_output=True, timeout=5)
        else:
            subprocess.run([
                "osascript", "-e",
                f'tell application "Music" to set player position to (player position - {abs_secs})'
            ], capture_output=True, timeout=5)
        return f"Seeked {direction} {abs_secs} seconds (Music)."
    except Exception:
        return f"Seek failed. No supported media player found."


def _seek_linux(seconds: int) -> str:
    direction = "forward" if seconds > 0 else "backward"
    abs_secs = abs(seconds)

    try:
        result = subprocess.run(
            ["playerctl", "position", f"{seconds:+d}"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return f"Seeked {direction} {abs_secs} seconds."
    except Exception:
        pass

    return f"Seek failed. Install 'playerctl' for precise seeking."


# ─── Now Playing ─────────────────────────────────────────────

def _now_playing_windows() -> str:
    """Get currently playing media info on Windows via PowerShell + SMTC API."""
    ps_script = r'''
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;

public class MediaInfo {
    public string Title { get; set; }
    public string Artist { get; set; }
    public string Album { get; set; }
    public string Status { get; set; }
    public string AppName { get; set; }
}

public static class NowPlaying {
    public static MediaInfo Get() {
        try {
            var t = Task.Run(async () => {
                var manager = await Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager.RequestAsync();
                var session = manager.GetCurrentSession();
                if (session == null) return new MediaInfo { Status = "nothing_playing" };

                var playback = session.GetPlaybackInfo();
                var media = await session.TryGetMediaPropertiesAsync();

                string status = "unknown";
                switch (playback.PlaybackStatus) {
                    case Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.Playing:
                        status = "playing"; break;
                    case Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.Paused:
                        status = "paused"; break;
                    case Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.Stopped:
                        status = "stopped"; break;
                    case Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.Changing:
                        status = "changing"; break;
                }

                return new MediaInfo {
                    Title = media.Title ?? "",
                    Artist = media.Artist ?? "",
                    Album = media.Album ?? "",
                    Status = status,
                    AppName = session.SourceAppUserModelId ?? ""
                };
            });
            t.Wait(TimeSpan.FromSeconds(5));
            return t.Result;
        } catch {
            return new MediaInfo { Status = "error" };
        }
    }
}
"@ -ReferencedAssemblies System.Runtime, System.Threading, System.Threading.Tasks -Language CSharp

$info = [NowPlaying]::Get()
if ($info.Status -eq "nothing_playing") {
    Write-Output "NO_MEDIA"
} elseif ($info.Status -eq "error") {
    Write-Output "ERROR"
} else {
    Write-Output "$($info.Title)|$($info.Artist)|$($info.Album)|$($info.Status)|$($info.AppName)"
}
'''
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.strip()

        if not output or output == "NO_MEDIA":
            return "No media is currently playing."
        if output == "ERROR":
            return "Could not retrieve media info."

        parts = output.split("|")
        title = parts[0] if len(parts) > 0 else "Unknown"
        artist = parts[1] if len(parts) > 1 else "Unknown"
        album = parts[2] if len(parts) > 2 else ""
        status = parts[3] if len(parts) > 3 else "unknown"
        app = parts[4] if len(parts) > 4 else ""

        status_emoji = {"playing": "Playing", "paused": "Paused", "stopped": "Stopped"}.get(status, status)

        lines = [f"Now {status_emoji}:"]
        if title and title != "Unknown":
            lines.append(f"  Title: {title}")
        if artist and artist != "Unknown":
            lines.append(f"  Artist: {artist}")
        if album:
            lines.append(f"  Album: {album}")
        if app:
            # Clean up app name (e.g. "SpotifyAB.SpotifyMusic_zpdnekdrzrew0!Spotify" → "Spotify")
            clean_app = app.split("!")[-1] if "!" in app else app
            clean_app = clean_app.replace("_", " ").split(".")[0] if "." in clean_app else clean_app
            lines.append(f"  App: {clean_app}")

        return "\n".join(lines)

    except subprocess.TimeoutExpired:
        return "Media info request timed out."
    except Exception as e:
        return f"Could not get media info: {e}"


def _now_playing_macos() -> str:
    """Get currently playing media on macOS via AppleScript."""
    # Try Spotify first
    try:
        result = subprocess.run([
            "osascript", "-e",
            'tell application "Spotify"\n'
            '  set t to name of current track\n'
            '  set a to artist of current track\n'
            '  set al to album of current track\n'
            '  set s to player state\n'
            '  return t & "||" & a & "||" & al & "||" & s\n'
            'end tell'
        ], capture_output=True, text=True, timeout=5)

        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("||")
            title = parts[0] if len(parts) > 0 else "Unknown"
            artist = parts[1] if len(parts) > 1 else "Unknown"
            album = parts[2] if len(parts) > 2 else ""
            status = parts[3] if len(parts) > 3 else "unknown"

            lines = [f"Now {status}:"]
            lines.append(f"  Title: {title}")
            lines.append(f"  Artist: {artist}")
            if album:
                lines.append(f"  Album: {album}")
            lines.append(f"  App: Spotify")
            return "\n".join(lines)
    except Exception:
        pass

    # Try Apple Music
    try:
        result = subprocess.run([
            "osascript", "-e",
            'tell application "Music"\n'
            '  set t to name of current track\n'
            '  set a to artist of current track\n'
            '  set al to album of current track\n'
            '  set s to player state\n'
            '  return t & "||" & a & "||" & al & "||" & s\n'
            'end tell'
        ], capture_output=True, text=True, timeout=5)

        if result.returncode == 0 and result.stdout.strip():
            parts = result.stdout.strip().split("||")
            title = parts[0] if len(parts) > 0 else "Unknown"
            artist = parts[1] if len(parts) > 1 else "Unknown"
            album = parts[2] if len(parts) > 2 else ""
            status = parts[3] if len(parts) > 3 else "unknown"

            lines = [f"Now {status}:"]
            lines.append(f"  Title: {title}")
            lines.append(f"  Artist: {artist}")
            if album:
                lines.append(f"  Album: {album}")
            lines.append(f"  App: Music")
            return "\n".join(lines)
    except Exception:
        pass

    return "No media is currently playing."


def _now_playing_linux() -> str:
    """Get currently playing media on Linux via playerctl."""
    try:
        result = subprocess.run(
            ["playerctl", "status"],
            capture_output=True, text=True, timeout=5
        )
        status = result.stdout.strip().lower() if result.returncode == 0 else "stopped"

        if status in ("", "stopped"):
            return "No media is currently playing."

        title_result = subprocess.run(
            ["playerctl", "metadata", "title"],
            capture_output=True, text=True, timeout=5
        )
        artist_result = subprocess.run(
            ["playerctl", "metadata", "artist"],
            capture_output=True, text=True, timeout=5
        )
        album_result = subprocess.run(
            ["playerctl", "metadata", "album"],
            capture_output=True, text=True, timeout=5
        )

        title = title_result.stdout.strip() or "Unknown"
        artist = artist_result.stdout.strip() or "Unknown"
        album = album_result.stdout.strip() or ""

        lines = [f"Now {status}:"]
        lines.append(f"  Title: {title}")
        lines.append(f"  Artist: {artist}")
        if album:
            lines.append(f"  Album: {album}")

        return "\n".join(lines)
    except FileNotFoundError:
        return "Install 'playerctl' to get media info: sudo apt install playerctl"
    except Exception as e:
        return f"Could not get media info: {e}"


# ─── OS Dispatch Maps ────────────────────────────────────────

_PLAY_PAUSE = {"Windows": _play_pause_windows, "Darwin": _play_pause_macos, "Linux": _play_pause_linux}
_NEXT_TRACK = {"Windows": _next_track_windows, "Darwin": _next_track_macos, "Linux": _next_track_linux}
_PREV_TRACK = {"Windows": _previous_track_windows, "Darwin": _previous_track_macos, "Linux": _previous_track_linux}
_STOP = {"Windows": _stop_windows, "Darwin": _stop_macos, "Linux": _stop_linux}
_SEEK = {"Windows": _seek_windows, "Darwin": _seek_macos, "Linux": _seek_linux}
_NOW_PLAYING = {"Windows": _now_playing_windows, "Darwin": _now_playing_macos, "Linux": _now_playing_linux}


# ─── Main Entry Point ────────────────────────────────────────

def media_control(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """Main dispatch for media control actions.

    Supported actions:
      - play_pause   : Toggle play/pause on current media
      - next         : Skip to next track
      - previous     : Go back to previous track
      - stop         : Stop playback
      - seek         : Seek forward/backward by N seconds (use 'seconds' param)
      - now_playing  : Get info about currently playing media
    """
    params = parameters or {}
    action = params.get("action", "").strip().lower().replace(" ", "_").replace("-", "_")

    if not action:
        return "Please specify a media action: play_pause, next, previous, stop, seek, now_playing."

    print(f"[media_control] Action: {action}  OS: {_OS}")
    if player:
        player.write_log(f"[media_control] {action}")

    # ── play_pause ──
    if action in ("play_pause", "playpause", "pause", "play", "toggle"):
        fn = _PLAY_PAUSE.get(_OS)
        return fn() if fn else "Unsupported OS."

    # ── next ──
    if action in ("next", "next_track", "skip", "forward"):
        fn = _NEXT_TRACK.get(_OS)
        return fn() if fn else "Unsupported OS."

    # ── previous ──
    if action in ("previous", "prev", "prev_track", "back", "previous_track"):
        fn = _PREV_TRACK.get(_OS)
        return fn() if fn else "Unsupported OS."

    # ── stop ──
    if action in ("stop", "stop_playback"):
        fn = _STOP.get(_OS)
        return fn() if fn else "Unsupported OS."

    # ── seek ──
    if action in ("seek", "seek_forward", "seek_backward", "rewind", "fast_forward"):
        try:
            seconds = int(params.get("seconds", 10))
        except (ValueError, TypeError):
            seconds = 10

        if action in ("rewind", "seek_backward"):
            seconds = -abs(seconds)
        elif action in ("fast_forward", "seek_forward"):
            seconds = abs(seconds)

        fn = _SEEK.get(_OS)
        return fn(seconds) if fn else "Unsupported OS."

    # ── now_playing ──
    if action in ("now_playing", "now", "current", "what_playing", "whats_playing", "status"):
        fn = _NOW_PLAYING.get(_OS)
        return fn() if fn else "Unsupported OS."

    return (
        f"Unknown media action: '{action}'. "
        f"Available: play_pause, next, previous, stop, seek, now_playing."
    )

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
    """Get currently playing media info on Windows via PowerShell + SMTC API.

    Same fix as _get_current_media_app_windows: iterate over GetSessions()
    instead of using GetCurrentSession(). The latter only returns the
    foreground app's session, which is wrong when media plays in the
    background (e.g. Spotify playing while Chrome is focused).
    """
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

                // Iterate ALL sessions, not just the foreground one.
                var sessions = manager.GetSessions();
                Windows.Media.Control.GlobalSystemMediaTransportControlsSession chosen = null;

                // First pass: prefer a Playing session.
                foreach (var s in sessions) {
                    try {
                        var info = s.GetPlaybackInfo();
                        if (info != null && info.PlaybackStatus ==
                            Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.Playing) {
                            chosen = s;
                            break;
                        }
                    } catch { }
                }
                // Second pass: accept Paused.
                if (chosen == null) {
                    foreach (var s in sessions) {
                        try {
                            var info = s.GetPlaybackInfo();
                            if (info != null && info.PlaybackStatus ==
                                Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.Paused) {
                                chosen = s;
                                break;
                            }
                        } catch { }
                    }
                }
                // Last resort: foreground session.
                if (chosen == null) {
                    chosen = manager.GetCurrentSession();
                }
                if (chosen == null) return new MediaInfo { Status = "nothing_playing" };

                var playback = chosen.GetPlaybackInfo();
                var media = await chosen.TryGetMediaPropertiesAsync();

                string status = "unknown";
                if (playback != null) {
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
                }

                return new MediaInfo {
                    Title = media.Title ?? "",
                    Artist = media.Artist ?? "",
                    Album = media.Album ?? "",
                    Status = status,
                    AppName = chosen.SourceAppUserModelId ?? ""
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


# ─── Volume (per-media / per-app) ───────────────────────────

def _volume_windows(value: int | None, mode: str) -> str:
    """Control the volume of the currently playing media on Windows.

    Strategy:
      1. Identify the currently playing app via the SMTC API (GetSessions
         + check PlaybackStatus == Playing). Returns SourceAppUserModelId.
      2. Use pycaw to find the audio session matching that app's process
         (via _find_pycaw_session_by_app helper).
      3. Fallback A: if SMTC reports nothing but pycaw has active audio
         sessions, use the most active one. This catches apps that don't
         register with SMTC (some browsers, VLC in certain modes, etc.).
      4. Fallback B: if no audio session at all, suggest using volume_set.

    `mode` is one of: "set", "up", "down", "mute", "unmute".
    `value` is 0-100 when mode is "set", ignored otherwise.
    """
    # Try pycaw first so we fail fast on missing dependency
    try:
        from pycaw.pycaw import AudioUtilities  # noqa: F401
    except ImportError:
        return (
            "pycaw is not installed. Install with: pip install pycaw comtypes "
            "— or use computer_settings volume_set for system-wide volume."
        )

    # Step 1: identify the playing app via SMTC
    app_name = _get_current_media_app_windows()

    target_session = None
    source = ""

    if app_name:
        # Step 2: find the pycaw session matching that app's process
        target_session = _find_pycaw_session_by_app(app_name)
        source = f"SMTC: {app_name}"

    # Fallback A: no SMTC match — try any active audio session.
    # The user clearly has audio playing, so we honour that.
    if target_session is None:
        target_session = _find_any_active_pycaw_session()
        if target_session is not None:
            try:
                proc_name = target_session.Process.name().replace(".exe", "")
            except Exception:
                proc_name = "active app"
            source = f"fallback: {proc_name}"
            if app_name:
                print(
                    f"[media_control] SMTC reported '{app_name}' but no pycaw "
                    f"session matched — falling back to active session {proc_name}."
                )
            else:
                print(
                    "[media_control] SMTC reported no playing session — falling "
                    f"back to active pycaw session {proc_name}."
                )

    if target_session is None:
        return (
            "No active audio session found. If media is playing, the app may "
            "not be registered with Windows media controls. "
            "Try computer_settings volume_set for system-wide volume."
        )

    # Compute the target scalar
    if mode == "mute":
        if _set_session_volume(target_session, scalar=0.0, mute=True):
            return f"Muted {source}."
        return f"Could not mute {source}."

    if mode == "unmute":
        # Restore to 50% if we don't know the previous level
        current = _get_session_volume(target_session) or 0.5
        if current <= 0.01:
            current = 0.5
        if _set_session_volume(target_session, scalar=current, mute=False):
            return f"Unmuted {source} (volume at {int(current * 100)}%)."
        return f"Could not unmute {source}."

    current = _get_session_volume(target_session)
    if current is None:
        current = 0.5

    if mode == "up":
        new_scalar = min(1.0, current + 0.1)
    elif mode == "down":
        new_scalar = max(0.0, current - 0.1)
    else:  # "set"
        new_scalar = max(0.0, min(1.0, (value or 0) / 100.0))

    if _set_session_volume(target_session, scalar=new_scalar, mute=False):
        return f"{source} volume set to {int(new_scalar * 100)}%."
    return f"Could not set volume for {source}."


def _volume_macos(value: int | None, mode: str) -> str:
    """Control media app volume on macOS via AppleScript.

    Spotify and Apple Music both expose a 'sound volume' property (0-100).
    """
    # Try Spotify first
    for app, label in [("Spotify", "Spotify"), ("Music", "Apple Music")]:
        try:
            if mode == "set":
                v = max(0, min(100, int(value or 50)))
                subprocess.run([
                    "osascript", "-e",
                    f'tell application "{app}" to set sound volume to {v}'
                ], capture_output=True, timeout=5)
                return f"{label} volume set to {v}%."
            if mode == "mute":
                subprocess.run([
                    "osascript", "-e",
                    f'tell application "{app}" to set sound volume to 0'
                ], capture_output=True, timeout=5)
                return f"Muted {label}."
            if mode == "unmute":
                # Restore to 50% since we don't know the previous value
                subprocess.run([
                    "osascript", "-e",
                    f'tell application "{app}" to set sound volume to 50'
                ], capture_output=True, timeout=5)
                return f"Unmuted {label} (restored to 50%)."
            # Relative modes
            script = f'set v to sound volume of application "{app}"'
            r = subprocess.run(["osascript", "-e", script],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and r.stdout.strip().isdigit():
                current = int(r.stdout.strip())
                if mode == "up":
                    new_v = min(100, current + 10)
                else:  # down
                    new_v = max(0, current - 10)
                subprocess.run([
                    "osascript", "-e",
                    f'tell application "{app}" to set sound volume to {new_v}'
                ], capture_output=True, timeout=5)
                return f"{label} volume set to {new_v}%."
        except Exception:
            continue

    return "No supported media app (Spotify, Music) is currently running."


def _volume_linux(value: int | None, mode: str) -> str:
    """Control media volume on Linux via playerctl."""
    try:
        if mode == "set":
            v = max(0.0, min(1.0, (value or 0) / 100.0))
            subprocess.run(["playerctl", "volume", f"{v}"],
                           capture_output=True, timeout=5)
            return f"Media volume set to {int(v * 100)}%."
        if mode == "mute":
            subprocess.run(["playerctl", "volume", "0.0"],
                           capture_output=True, timeout=5)
            return "Media muted."
        if mode == "unmute":
            subprocess.run(["playerctl", "volume", "0.5"],
                           capture_output=True, timeout=5)
            return "Media unmuted (restored to 50%)."
        # Relative modes — playerctl accepts "0.1+" / "0.1-"
        delta = "0.1+" if mode == "up" else "0.1-"
        subprocess.run(["playerctl", "volume", delta],
                       capture_output=True, timeout=5)
        # Read back the new value
        r = subprocess.run(["playerctl", "volume"],
                           capture_output=True, text=True, timeout=5)
        new_val = r.stdout.strip() if r.returncode == 0 else "?"
        return f"Media volume adjusted ({new_val})."
    except FileNotFoundError:
        return "Install 'playerctl' to control media volume: sudo apt install playerctl"
    except Exception as e:
        return f"Could not control media volume: {e}"


def _get_current_media_app_windows() -> str | None:
    """Identify the app currently playing media via the SMTC API.

    Returns a cleaned-up app name (e.g. "Spotify", "Chrome") or None if no
    media is playing.

    NOTE: We iterate over ALL SMTC sessions (GetSessions) and pick the first
    one with PlaybackStatus == Playing. The previous code used GetCurrentSession(),
    which only returns the session for the FOREGROUND app — so if Spotify was
    playing in the background while Chrome was focused, GetCurrentSession()
    returned Chrome's session (which has no media) and we'd report "nothing
    playing". GetSessions() returns every registered media session, regardless
    of which app is in the foreground.
    """
    ps_script = r'''
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;
using System.Linq;

public static class CurrentMediaApp {
    public static string Get() {
        try {
            var t = Task.Run(async () => {
                var manager = await Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager.RequestAsync();
                // Try GetSessions() — returns ALL registered media sessions.
                var sessions = manager.GetSessions();
                // First pass: find a session that is actually Playing.
                foreach (var s in sessions) {
                    try {
                        var info = s.GetPlaybackInfo();
                        if (info != null && info.PlaybackStatus ==
                            Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.Playing) {
                            return s.SourceAppUserModelId ?? "";
                        }
                    } catch { }
                }
                // Second pass: accept Paused sessions (user might want to control
                // the volume of a paused player too).
                foreach (var s in sessions) {
                    try {
                        var info = s.GetPlaybackInfo();
                        if (info != null && info.PlaybackStatus ==
                            Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus.Paused) {
                            return s.SourceAppUserModelId ?? "";
                        }
                    } catch { }
                }
                // Last resort: GetCurrentSession() (foreground app).
                var current = manager.GetCurrentSession();
                if (current != null) return current.SourceAppUserModelId ?? "";
                return "";
            });
            t.Wait(TimeSpan.FromSeconds(5));
            return t.Result;
        } catch {
            return "";
        }
    }
}
"@ -ReferencedAssemblies System.Runtime, System.Threading, System.Threading.Tasks -Language CSharp

$result = [CurrentMediaApp]::Get()
Write-Output $result
'''
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, text=True, timeout=10
        )
        raw = result.stdout.strip()
        if not raw:
            return None
        # Clean up app identifiers like "SpotifyAB.SpotifyMusic_zpdnekdrzrew0!Spotify"
        clean = raw.split("!")[-1] if "!" in raw else raw
        clean = clean.replace("_", " ").split(".")[0] if "." in clean else clean
        return clean.strip() or None
    except Exception as e:
        print(f"[media_control] _get_current_media_app_windows error: {e}")
        return None


def _find_pycaw_session_by_app(app_name: str):
    """Find a pycaw AudioSession whose process name matches `app_name`.

    Returns the AudioSession object (with .ProcessId, ._ctl etc.) or None.
    Uses pycaw's public AudioUtilities.GetAllSessions() API.
    """
    try:
        from pycaw.pycaw import AudioUtilities
        import psutil

        app_query = (app_name or "").lower().replace(".exe", "").strip()
        if not app_query:
            return None

        for sess in AudioUtilities.GetAllSessions():
            try:
                pid = sess.ProcessId
                if pid == 0 or sess.Process is None:
                    continue
                proc_name = sess.Process.name().lower().replace(".exe", "")
                # Partial match in either direction
                if app_query in proc_name or proc_name in app_query:
                    return sess
            except Exception:
                continue
        return None
    except ImportError:
        return None
    except Exception:
        return None


def _find_any_active_pycaw_session():
    """Find any active pycaw AudioSession — used as last-resort fallback when
    SMTC detection fails but the user is clearly playing audio.

    Returns the most recently-active session or None.
    """
    try:
        from pycaw.pycaw import AudioUtilities
        sessions = AudioUtilities.GetAllSessions()
        # Prefer sessions with a real process (not system sounds)
        candidates = [s for s in sessions if s.ProcessId and s.Process is not None
                      and s.Process.name().lower() not in ("audiodg.exe", "explorer.exe")]
        return candidates[0] if candidates else None
    except ImportError:
        return None
    except Exception:
        return None


def _set_session_volume(session, scalar: float, mute: bool | None = None) -> bool:
    """Set the volume (and optionally mute state) of a pycaw AudioSession.

    Args:
        session: a pycaw AudioSession
        scalar:  0.0 - 1.0
        mute:    None to leave mute unchanged, True/False to set
    Returns True on success.
    """
    try:
        from pycaw.pycaw import ISimpleAudioVolume
        vol = session._ctl.QueryInterface(ISimpleAudioVolume)
        vol.SetMasterVolume(float(scalar), None)
        if mute is not None:
            vol.SetMute(bool(mute), None)
        return True
    except Exception as e:
        print(f"[media_control] _set_session_volume error: {e}")
        return False


def _get_session_volume(session) -> float | None:
    """Get the current volume scalar (0.0 - 1.0) of a pycaw AudioSession."""
    try:
        from pycaw.pycaw import ISimpleAudioVolume
        vol = session._ctl.QueryInterface(ISimpleAudioVolume)
        return vol.GetMasterVolume()
    except Exception:
        return None


# ─── OS Dispatch Maps ────────────────────────────────────────

_PLAY_PAUSE = {"Windows": _play_pause_windows, "Darwin": _play_pause_macos, "Linux": _play_pause_linux}
_NEXT_TRACK = {"Windows": _next_track_windows, "Darwin": _next_track_macos, "Linux": _next_track_linux}
_PREV_TRACK = {"Windows": _previous_track_windows, "Darwin": _previous_track_macos, "Linux": _previous_track_linux}
_STOP = {"Windows": _stop_windows, "Darwin": _stop_macos, "Linux": _stop_linux}
_SEEK = {"Windows": _seek_windows, "Darwin": _seek_macos, "Linux": _seek_linux}
_NOW_PLAYING = {"Windows": _now_playing_windows, "Darwin": _now_playing_macos, "Linux": _now_playing_linux}
_VOLUME = {"Windows": _volume_windows, "Darwin": _volume_macos, "Linux": _volume_linux}


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
      - volume       : Control the volume of the currently playing media app.
                       Use 'value' (0-100) to set, or sub-action via 'mode':
                       "set" (default, requires value), "up", "down",
                       "mute", "unmute".
                       On Windows this targets the per-app audio session
                       (Volume Mixer slider), independent of system volume.
                       For SYSTEM-WIDE volume, use computer_settings volume_set.
    """
    params = parameters or {}
    action = params.get("action", "").strip().lower().replace(" ", "_").replace("-", "_")

    if not action:
        return "Please specify a media action: play_pause, next, previous, stop, seek, now_playing, volume."

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

    # ── volume ──
    if action in ("volume", "media_volume", "app_volume", "set_volume", "media_volume_set"):
        # Determine the sub-mode:
        #   - explicit "mode" param wins
        #   - else if value provided → "set"
        #   - else default to "up" (safe no-op-ish toggle)
        mode = (params.get("mode") or "").strip().lower()
        value = params.get("value")
        try:
            value_int = int(value) if value is not None else None
        except (ValueError, TypeError):
            value_int = None

        if not mode:
            if value_int is not None:
                mode = "set"
            else:
                return (
                    "Please specify a volume mode: set (with value 0-100), up, down, mute, or unmute. "
                    "Example: action=volume, value=50  →  set to 50%."
                )

        if mode in ("set", "set_volume", "to"):
            if value_int is None:
                return "Volume 'set' mode requires a value (0-100)."
            mode = "set"
        elif mode in ("up", "increase", "louder", "+"):
            mode = "up"
        elif mode in ("down", "decrease", "lower", "quieter", "-"):
            mode = "down"
        elif mode in ("mute", "silence", "0"):
            mode = "mute"
        elif mode in ("unmute", "restore"):
            mode = "unmute"
        else:
            return f"Unknown volume mode: '{mode}'. Use set/up/down/mute/unmute."

        fn = _VOLUME.get(_OS)
        return fn(value_int, mode) if fn else "Unsupported OS."

    return (
        f"Unknown media action: '{action}'. "
        f"Available: play_pause, next, previous, stop, seek, now_playing, volume."
    )

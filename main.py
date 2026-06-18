import asyncio
import threading
import json
import sys
import traceback
from pathlib import Path

import sounddevice as sd
from google import genai
from google.genai import types
from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory
)

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app, close_app, list_open_apps
from actions.weather_report    import weather_action
from actions.send_message      import send_message
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import screen_process
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.cmd_control       import cmd_control
from actions.game_updater      import game_updater
from actions.discord_control   import discord_control
from actions.media_control     import media_control
from actions.auto_click        import auto_click


def get_base_dir():
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent


BASE_DIR        = get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"
PROMPT_PATH     = BASE_DIR / "core" / "prompt.txt"

# Default Live API model — preview audio model that supports realtime voice.
# Can be overridden via the "live_model" field in config/api_keys.json
# (with or without the "models/" prefix).
LIVE_MODEL_DEFAULT  = "models/gemini-2.5-flash-native-audio-preview-12-2025"

# Fallback chain — if the primary model is rejected (e.g. 1008 policy violation
# because the preview was deprecated), Jarvis tries each of these in order.
# The last known-good model is sticky: once one succeeds, it's reused on
# reconnects until it fails.
LIVE_MODEL_FALLBACKS = [
    "models/gemini-2.5-flash-native-audio-preview-12-2025",
    "models/gemini-live-2.5-flash-preview",
    "models/gemini-2.0-flash-live-001",
    "models/gemini-2.0-flash-exp-native-audio",
]

CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 1024


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _load_live_model() -> str:
    """Load the Live API model name from config/api_keys.json.

    Falls back to LIVE_MODEL_DEFAULT if missing or unreadable.
    The "models/" prefix is added automatically if the user typed just the
    model slug.
    """
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        model = (data.get("live_model") or "").strip()
        if not model:
            return LIVE_MODEL_DEFAULT
        return model if model.startswith("models/") else f"models/{model}"
    except Exception:
        return LIVE_MODEL_DEFAULT


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )
    
_last_memory_input = ""

def _update_memory_async(user_text: str, jarvis_text: str) -> None:
    global _last_memory_input

    user_text   = (user_text   or "").strip()
    jarvis_text = (jarvis_text or "").strip()

    if len(user_text) < 5 or user_text == _last_memory_input:
        return
    _last_memory_input = user_text

    try:
        api_key = _get_api_key()
        if not should_extract_memory(user_text, jarvis_text, api_key):
            return
        data = extract_memory(user_text, jarvis_text, api_key)
        if data:
            update_memory(data)
            print(f"[Memory] ✅ {list(data.keys())}")
    except Exception as e:
        if "429" not in str(e):
            print(f"[Memory] ⚠️ {e}")

TOOL_DECLARATIONS = [
    {
        "name": "open_app",
        "description": (
            "Opens any application on the computer. "
            "Use this whenever the user asks to open, launch, or start any app, "
            "website, or program. Always call this tool — never just say you opened it."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Exact name of the application (e.g. 'WhatsApp', 'Chrome', 'Spotify')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "close_app",
        "description": (
            "Closes a specific application by name. Uses taskkill on Windows, pkill on Mac/Linux. "
            "Does NOT use Alt+F4 — targets the exact process. "
            "Use when user asks to close, quit, or exit an app. "
            "For Discord, use discord_control instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "app_name": {
                    "type": "STRING",
                    "description": "Name of the application to close (e.g. 'Chrome', 'Spotify', 'Discord')"
                }
            },
            "required": ["app_name"]
        }
    },
    {
        "name": "list_open_apps",
        "description": (
            "Lists all currently running applications. "
            "Use when the user asks what apps are open, what's running, or wants to see open programs."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {},
            "required": []
        }
    },
    {
        "name": "media_control",
        "description": (
            "Controls media playback on the system. Works system-wide regardless of which app is focused. "
            "Use for: play/pause music or videos, skip to next track, go back to previous track, "
            "stop playback, seek forward or backward, check what's currently playing, AND adjust the "
            "volume of the currently playing media app (per-app, NOT system-wide). "
            "Supports Spotify, YouTube Music, Apple Music, VLC, and any media app. "
            "Use this when the user says: pause, play, skip, next, previous, what's playing, "
            "qu'est-ce qui joue, met pause, passe, reviens, baisse la musique, monte le son de Spotify, etc. "
            "For SYSTEM-WIDE volume, use computer_settings volume_set instead. "
            "For per-app volume targeting a SPECIFIC app by name (not necessarily the playing one), "
            "use computer_settings set_app_volume."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {
                    "type": "STRING",
                    "description": (
                        "play_pause | next | previous | stop | seek | now_playing | volume  "
                        "(default: play_pause)"
                    )
                },
                "seconds": {
                    "type": "INTEGER",
                    "description": "Seconds to seek forward (positive) or backward (negative). Default: 10"
                },
                "value": {
                    "type": "INTEGER",
                    "description": "For volume action: target volume 0-100 (only used when mode=set)."
                },
                "mode": {
                    "type": "STRING",
                    "description": "Sub-action for volume: set (default if value given) | up | down | mute | unmute."
                },
            },
            "required": []
        }
    },
    {
        "name": "web_search",
        "description": "Searches the web for any information.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query":  {"type": "STRING", "description": "Search query"},
                "mode":   {"type": "STRING", "description": "search (default) or compare"},
                "items":  {"type": "ARRAY", "items": {"type": "STRING"}, "description": "Items to compare"},
                "aspect": {"type": "STRING", "description": "price | specs | reviews"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "weather_report",
        "description": "Gives the weather report to user",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "city": {"type": "STRING", "description": "City name"}
            },
            "required": ["city"]
        }
    },
    {
        "name": "send_message",
        "description": (
            "Sends a text message via WhatsApp, Telegram, or other messaging platform. "
            "NOT for Discord — use discord_control for all Discord actions."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "receiver":     {"type": "STRING", "description": "Recipient contact name"},
                "message_text": {"type": "STRING", "description": "The message to send"},
                "platform":     {"type": "STRING", "description": "Platform: WhatsApp, Telegram, etc. NOT Discord."}
            },
            "required": ["receiver", "message_text", "platform"]
        }
    },
    {
        "name": "discord_control",
        "description": (
            "Controls the user's personal Discord account. Can send DMs, send messages in server channels, "
            "read channel messages, read DM messages, list servers, list channels, list friends, list all DM "
            "conversations, and check connection status. Use for ANY Discord action. "
            "NOT for WhatsApp/Telegram — use send_message for those."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":   {"type": "STRING", "description": "send_dm | send_channel | read_channel | read_dm | list_servers | list_channels | list_friends | list_dms | status (default: status)"},
                "receiver": {"type": "STRING", "description": "Username, global name, user ID, or DM channel ID (for send_dm, read_dm)"},
                "server":   {"type": "STRING", "description": "Server name (for send_channel, read_channel, list_channels)"},
                "channel":  {"type": "STRING", "description": "Channel name (for send_channel, read_channel)"},
                "message":  {"type": "STRING", "description": "Message text to send (for send_dm, send_channel)"},
                "limit":    {"type": "INTEGER", "description": "Number of messages to read (for read_channel and read_dm, default: 10). For list_dms, number of recent messages to show per conversation (default: 5, max: 10). When >= 3, only the 10 most recent conversations are listed to keep the response readable."}
            },
            "required": ["action"]
        }
    },
    {
        "name": "reminder",
        "description": "Sets a timed reminder using Windows Task Scheduler.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "date":    {"type": "STRING", "description": "Date in YYYY-MM-DD format"},
                "time":    {"type": "STRING", "description": "Time in HH:MM format (24h)"},
                "message": {"type": "STRING", "description": "Reminder message text"}
            },
            "required": ["date", "time", "message"]
        }
    },
    {
        "name": "youtube_video",
        "description": (
            "Controls YouTube, YouTube Music, and Spotify. "
            "Use for: playing videos, playing music, summarizing a video's content, "
            "getting video info, or showing trending videos. "
            "CRITICAL: Use platform='youtube_music' when the user asks to play MUSIC, "
            "listen to a song, or anything music-related. "
            "Use platform='youtube' (default) only for videos. "
            "Use platform='spotify' when the user explicitly mentions Spotify. "
            "Examples: 'joue musique X' → platform=youtube_music, 'regarde vidéo X' → platform=youtube, "
            "'lance sur Spotify X' → platform=spotify."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":   {"type": "STRING", "description": "play | summarize | get_info | trending (default: play)"},
                "query":    {"type": "STRING", "description": "Search query for play action"},
                "platform": {"type": "STRING", "description": "youtube | youtube_music | spotify (default: youtube). Use youtube_music for music/songs, youtube for videos, spotify when user mentions Spotify."},
                "save":     {"type": "BOOLEAN", "description": "Save summary to Notepad (summarize only)"},
                "region":   {"type": "STRING", "description": "Country code for trending e.g. TR, US"},
                "url":      {"type": "STRING", "description": "Video URL for get_info action"},
            },
            "required": []
        }
    },
    {
        "name": "screen_process",
        "description": (
            "Captures and analyzes the screen or webcam image. "
            "MUST be called when user asks what is on screen, what you see, "
            "analyze my screen, look at camera, etc. "
            "You have NO visual ability without this tool. "
            "After calling this tool, stay SILENT — the vision module speaks directly."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "angle": {"type": "STRING", "description": "'screen' to capture display, 'camera' for webcam. Default: 'screen'"},
                "text":  {"type": "STRING", "description": "The question or instruction about the captured image"}
            },
            "required": ["text"]
        }
    },
    {
        "name": "computer_settings",
        "description": (
            "Controls the computer: volume, brightness, window management, keyboard shortcuts, "
            "typing text on screen, closing apps, fullscreen, dark mode, WiFi, restart, shutdown, "
            "scrolling, tab management, zoom, screenshots, lock screen, refresh/reload page. "
            "Volume actions: volume_set (system-wide master 0-100), set_app_volume (per-app Volume "
            "Mixer slider, requires app_name + value 0-100). "
            "Use for ANY single computer control command. NEVER route to agent_task."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "The action to perform. Volume-related: volume_set, set_app_volume. See ACTION_MAP in source for full list."},
                "description": {"type": "STRING", "description": "Natural language description of what to do (used for LLM intent routing when 'action' is empty)"},
                "value":       {"type": "STRING", "description": "Optional value: volume level (0-100), text to type, etc."},
                "app_name":    {"type": "STRING", "description": "For set_app_volume: target application name (e.g. 'Spotify', 'Discord', 'chrome'). Match is case-insensitive and partial."}
            },
            "required": []
        }
    },
    {
        "name": "browser_control",
        "description": (
            "Controls the web browser. Uses CMD/system default browser for opening URLs (NOT Chrome for Testing). "
            "Use action:open_url to open a URL in the real default browser. "
            "Use action:search to search the web in the real default browser. "
            "Use Playwright actions (click, type, scroll, etc.) ONLY for interactive web automation. "
            "CRITICAL: For simply opening a website or URL, ALWAYS use action:open_url — NEVER use Playwright. "
            "CRITICAL: For searching the web, ALWAYS use action:search — NEVER use Playwright."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "open_url | go_to | search | click | type | scroll | fill_form | smart_click | smart_type | get_text | press | close"},
                "url":         {"type": "STRING", "description": "URL for open_url/go_to"},
                "query":       {"type": "STRING", "description": "Search query"},
                "engine":      {"type": "STRING", "description": "google | bing | duckduckgo (default: google)"},
                "selector":    {"type": "STRING", "description": "CSS selector for click/type"},
                "text":        {"type": "STRING", "description": "Text to click or type"},
                "description": {"type": "STRING", "description": "Element description for smart_click/smart_type"},
                "direction":   {"type": "STRING", "description": "up or down for scroll"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount in pixels (default: 500)"},
                "key":         {"type": "STRING", "description": "Key name for press action"},
                "fields":      {"type": "OBJECT", "description": "{selector: value} dict for fill_form"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "file_controller",
        "description": "Manages files and folders: list, create, delete, move, copy, rename, read, write, find, disk usage.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "list | create_file | create_folder | delete | move | copy | rename | read | write | find | largest | disk_usage | organize_desktop | info"},
                "path":        {"type": "STRING", "description": "File/folder path or shortcut: desktop, downloads, documents, home"},
                "destination": {"type": "STRING", "description": "Destination path for move/copy"},
                "new_name":    {"type": "STRING", "description": "New name for rename"},
                "content":     {"type": "STRING", "description": "Content for create_file/write"},
                "name":        {"type": "STRING", "description": "File name to search for"},
                "extension":   {"type": "STRING", "description": "File extension to search (e.g. .pdf)"},
                "count":       {"type": "INTEGER", "description": "Number of results for largest"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "desktop_control",
        "description": "Controls the desktop: wallpaper, organize, clean, list, stats.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action": {"type": "STRING", "description": "wallpaper | wallpaper_url | organize | clean | list | stats | task"},
                "path":   {"type": "STRING", "description": "Image path for wallpaper"},
                "url":    {"type": "STRING", "description": "Image URL for wallpaper_url"},
                "mode":   {"type": "STRING", "description": "by_type or by_date for organize"},
                "task":   {"type": "STRING", "description": "Natural language desktop task"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "code_helper",
        "description": "Writes, edits, explains, runs, or builds code files.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "write | edit | explain | run | build | auto (default: auto)"},
                "description": {"type": "STRING", "description": "What the code should do or what change to make"},
                "language":    {"type": "STRING", "description": "Programming language (default: python)"},
                "output_path": {"type": "STRING", "description": "Where to save the file"},
                "file_path":   {"type": "STRING", "description": "Path to existing file for edit/explain/run/build"},
                "code":        {"type": "STRING", "description": "Raw code string for explain"},
                "args":        {"type": "STRING", "description": "CLI arguments for run/build"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "dev_agent",
        "description": "Builds complete multi-file projects from scratch: plans, writes files, installs deps, opens VSCode, runs and fixes errors.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "description":  {"type": "STRING", "description": "What the project should do"},
                "language":     {"type": "STRING", "description": "Programming language (default: python)"},
                "project_name": {"type": "STRING", "description": "Optional project folder name"},
                "timeout":      {"type": "INTEGER", "description": "Run timeout in seconds (default: 30)"},
            },
            "required": ["description"]
        }
    },
    {
        "name": "agent_task",
        "description": (
            "Executes complex multi-step tasks requiring multiple different tools. "
            "Examples: 'research X and save to file', 'find and organize files'. "
            "DO NOT use for single commands. NEVER use for Steam/Epic — use game_updater."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "goal":     {"type": "STRING", "description": "Complete description of what to accomplish"},
                "priority": {"type": "STRING", "description": "low | normal | high (default: normal)"}
            },
            "required": ["goal"]
        }
    },
    {
        "name": "computer_control",
        "description": "Direct computer control: type, click, hotkeys, scroll, move mouse, screenshots, find and interact with UI elements on screen by name.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "type | smart_type | click | double_click | right_click | hotkey | press | scroll | move | copy | paste | screenshot | wait | clear_field | focus_window | screen_find | screen_click | ui_click | ui_type | ui_find | ui_list | random_data | user_data"},
                "text":        {"type": "STRING", "description": "Text to type or paste"},
                "x":           {"type": "INTEGER", "description": "X coordinate"},
                "y":           {"type": "INTEGER", "description": "Y coordinate"},
                "keys":        {"type": "STRING", "description": "Key combination e.g. 'ctrl+c'"},
                "key":         {"type": "STRING", "description": "Single key e.g. 'enter'"},
                "direction":   {"type": "STRING", "description": "up | down | left | right"},
                "amount":      {"type": "INTEGER", "description": "Scroll amount (default: 3)"},
                "seconds":     {"type": "NUMBER",  "description": "Seconds to wait"},
                "title":       {"type": "STRING",  "description": "Window title for focus_window"},
                "description": {"type": "STRING",  "description": "Element description for screen_find/screen_click, or element name for ui_click/ui_type/ui_find"},
                "name":        {"type": "STRING",  "description": "UI element name for ui_click, ui_type, ui_find (e.g. 'Save', 'OK', 'Search')"},
                "element_type":{"type": "STRING",  "description": "UI element type filter: button | input | link | checkbox | tab | menu | combo | any (default: any)"},
                "click_type":  {"type": "STRING",  "description": "For ui_click: left | right | double (default: left)"},
                "index":       {"type": "INTEGER", "description": "Index when multiple elements match (default: 0)"},
                "filter":      {"type": "STRING",  "description": "Text filter for ui_list"},
                "type":        {"type": "STRING",  "description": "Data type for random_data"},
                "field":       {"type": "STRING",  "description": "Field for user_data: name|email|city"},
                "clear_first": {"type": "BOOLEAN", "description": "Clear field before typing (default: true)"},
                "path":        {"type": "STRING",  "description": "Save path for screenshot"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "auto_click",
        "description": (
            "Smart auto-click that finds and clicks a UI element using multiple strategies automatically. "
            "Tries UI Automation first, then AI vision (screenshot analysis), then browser smart click. "
            "Supports repeated clicking with intervals and SPATIAL POSITIONING. "
            "When multiple matching elements exist (e.g. 6 videos), use 'position' to specify which one: "
            "by number (1-9), ordinal (first, second, 3rd), or spatial keyword (top-left, center, bottom-right). "
            "The AI will find ALL instances, sort them in reading order, and click the one at the requested position. "
            "Use when user asks to click something by name/description, click a specific item among several, or auto-click repeatedly. "
            "More reliable than computer_control's screen_click or ui_click alone because it chains fallbacks. "
            "For simple coordinate clicks, use computer_control instead."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "target":       {"type": "STRING",  "description": "Name or description of the element to click (e.g. 'video', 'Save button', 'Submit', 'Accept cookies')"},
                "position":     {"type": "STRING",  "description": "Which instance to click when multiple matches exist. Number: '1'-'9'. Ordinal: 'first','second','3rd','4th'. Spatial: 'top-left','top-right','center','bottom-left','bottom-right'. French: 'premier','deuxieme','troisieme','haut-gauche','bas-droite'. Triggers AI vision grid mode."},
                "strategy":     {"type": "STRING",  "description": "Click strategy: auto | ui | vision | browser | ui_vision | spatial (default: auto). 'auto' tries all in order. 'spatial' forces grid mode."},
                "click_type":   {"type": "STRING",  "description": "Click type: left | right | double (default: left)"},
                "element_type": {"type": "STRING",  "description": "Element type filter: button | link | input | checkbox | tab | menu | any (default: any)"},
                "index":        {"type": "INTEGER", "description": "Index when multiple elements match (default: 0)"},
                "count":        {"type": "INTEGER", "description": "Number of times to click (default: 1, max: 100)"},
                "interval":     {"type": "NUMBER",  "description": "Seconds between repeated clicks (default: 1.0, range: 0.1-30)"},
            },
            "required": ["target"]
        }
    },
    {
        "name": "cmd_control",
        "description": (
            "Executes terminal/CMD commands, opens files, opens memory, manages apps, AND can modify Jarvis's own source code. "
            "Use for: running shell commands, piped commands, background processes, "
            "listing/killing processes, network info, disk usage, system info, "
            "opening files (open_file, open_project_file), opening the long-term memory file (open_memory), "
            "installing applications (install_app), uninstalling applications (uninstall_app), listing installed apps (list_installed_apps), "
            "AND reading/writing/appending the Jarvis's own Python files (self_read, self_write, self_append, self_list). "
            "Has built-in safety checks against dangerous commands. "
            "Use this when the user asks to run a terminal command, CMD command, "
            "open a file, open their memory, install or uninstall an app, modify Jarvis's code, add features, fix bugs in itself. "
            "CRITICAL: Always use action:open_memory when user asks to open their memory/long-term memory. "
            "CRITICAL: Always use action:install_app when user asks to install an application. "
            "CRITICAL: Always use action:uninstall_app when user asks to uninstall an application."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":      {"type": "STRING", "description": "run | run_in_dir | run_piped | run_background | list_processes | kill_process | network_info | disk_usage | system_info | open_file | open_project_file | open_memory | install_app | uninstall_app | list_installed_apps | self_read | self_write | self_append | self_list"},
                "command":     {"type": "STRING", "description": "The command string to execute"},
                "working_dir": {"type": "STRING", "description": "Working directory for run_in_dir"},
                "timeout":     {"type": "INTEGER", "description": "Execution timeout in seconds (default: 30, max: 120)"},
                "filter":      {"type": "STRING", "description": "Process name filter for list_processes / app name filter for list_installed_apps"},
                "process":     {"type": "STRING", "description": "PID or process name for kill_process"},
                "path":        {"type": "STRING", "description": "File path for open_file, open_project_file, and self_* actions"},
                "app_name":    {"type": "STRING", "description": "Application name for install_app / uninstall_app"},
                "content":     {"type": "STRING", "description": "File content to write/append (for self_write/self_append)"},
            },
            "required": ["action"]
        }
    },
    {
        "name": "game_updater",
        "description": (
            "THE ONLY tool for ANY Steam or Epic Games request. "
            "Use for: installing, downloading, updating games, listing installed games, "
            "checking download status, scheduling updates. "
            "ALWAYS call directly for any Steam/Epic/game request. "
            "NEVER use agent_task, browser_control, or web_search for Steam/Epic."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "action":    {"type": "STRING",  "description": "update | install | list | download_status | schedule | cancel_schedule | schedule_status (default: update)"},
                "platform":  {"type": "STRING",  "description": "steam | epic | both (default: both)"},
                "game_name": {"type": "STRING",  "description": "Game name (partial match supported)"},
                "app_id":    {"type": "STRING",  "description": "Steam AppID for install (optional)"},
                "hour":      {"type": "INTEGER", "description": "Hour for scheduled update 0-23 (default: 3)"},
                "minute":    {"type": "INTEGER", "description": "Minute for scheduled update 0-59 (default: 0)"},
                "shutdown_when_done": {"type": "BOOLEAN", "description": "Shut down PC when download finishes"},
            },
            "required": []
        }
    },
    {
        "name": "flight_finder",
        "description": "Searches Google Flights and speaks the best options.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "origin":      {"type": "STRING",  "description": "Departure city or airport code"},
                "destination": {"type": "STRING",  "description": "Arrival city or airport code"},
                "date":        {"type": "STRING",  "description": "Departure date (any format)"},
                "return_date": {"type": "STRING",  "description": "Return date for round trips"},
                "passengers":  {"type": "INTEGER", "description": "Number of passengers (default: 1)"},
                "cabin":       {"type": "STRING",  "description": "economy | premium | business | first"},
                "save":        {"type": "BOOLEAN", "description": "Save results to Notepad"},
            },
            "required": ["origin", "destination", "date"]
        }
    },
    {
    "name": "file_processor",
    "description": (
        "Processes any file that the user has uploaded or dropped onto the interface. "
        "Use this when the user refers to an uploaded file and wants an action on it. "
        "Supports: images (describe/ocr/resize/compress/convert), "
        "PDFs (summarize/extract_text/to_word), "
        "Word docs & text files (summarize/fix/reformat/translate), "
        "CSV/Excel (analyze/stats/filter/sort/convert), "
        "JSON/XML (validate/format/analyze), "
        "code files (explain/review/fix/optimize/run/document/test), "
        "audio (transcribe/trim/convert/info), "
        "video (trim/extract_audio/extract_frame/compress/transcribe/info), "
        "archives (list/extract), "
        "presentations (summarize/extract_text). "
        "ALWAYS call this tool when a file has been uploaded and the user gives a command about it. "
        "If the user's command is ambiguous, pick the most logical action for that file type."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "file_path": {
                "type": "STRING",
                "description": "Full path to the uploaded file. Leave empty to use the currently uploaded file."
            },
            "action": {
                "type": "STRING",
                "description": (
                    "What to do with the file. Examples by type:\n"
                    "image: describe | ocr | resize | compress | convert | info\n"
                    "pdf: summarize | extract_text | to_word | info\n"
                    "docx/txt: summarize | fix | reformat | translate_hint | word_count | to_bullet\n"
                    "csv/excel: analyze | stats | filter | sort | convert | info\n"
                    "json: validate | format | analyze | to_csv\n"
                    "code: explain | review | fix | optimize | run | document | test\n"
                    "audio: transcribe | trim | convert | info\n"
                    "video: trim | extract_audio | extract_frame | compress | transcribe | info | convert\n"
                    "archive: list | extract\n"
                    "pptx: summarize | extract_text | analyze"
                )
            },
            "instruction": {
                "type": "STRING",
                "description": "Free-form instruction if action doesn't cover it. E.g. 'translate this to Turkish', 'find all email addresses'"
            },
            "format": {
                "type": "STRING",
                "description": "Target format for conversion. E.g. 'mp3', 'pdf', 'csv', 'png'"
            },
            "width":     {"type": "INTEGER", "description": "Target width for image resize"},
            "height":    {"type": "INTEGER", "description": "Target height for image resize"},
            "scale":     {"type": "NUMBER",  "description": "Scale factor for image resize (e.g. 0.5)"},
            "quality":   {"type": "INTEGER", "description": "Quality 1-100 for image/video compress"},
            "start":     {"type": "STRING",  "description": "Start time for trim: seconds or HH:MM:SS"},
            "end":       {"type": "STRING",  "description": "End time for trim: seconds or HH:MM:SS"},
            "timestamp": {"type": "STRING",  "description": "Timestamp for video frame extraction HH:MM:SS"},
            "column":    {"type": "STRING",  "description": "Column name for CSV filter/sort"},
            "value":     {"type": "STRING",  "description": "Filter value for CSV filter"},
            "condition": {"type": "STRING",  "description": "Filter condition: equals|contains|gt|lt"},
            "ascending": {"type": "BOOLEAN", "description": "Sort order for CSV sort (default: true)"},
            "save":      {"type": "BOOLEAN", "description": "Save result to file (default: true)"},
            "destination": {"type": "STRING", "description": "Output folder for archive extract"},
        },
        "required": []
    }
},
    {
    "name": "shutdown_jarvis",
    "description": (
        "Shuts down the assistant completely. "
        "Call this when the user expresses intent to end the conversation, "
        "close the assistant, say goodbye, or stop Jarvis. "
        "The user can say this in ANY language."
    ),
    "parameters": {
        "type": "OBJECT",
        "properties": {},
    }
    },
    {
        "name": "save_memory",
        "description": (
            "Save an important personal fact about the user to long-term memory. "
            "Call this silently whenever the user reveals something worth remembering: "
            "name, age, city, job, preferences, hobbies, relationships, projects, or future plans. "
            "Do NOT call for: weather, reminders, searches, or one-time commands. "
            "Do NOT announce that you are saving — just call it silently. "
            "Values must be in English regardless of the conversation language."
        ),
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "category": {
                    "type": "STRING",
                    "description": (
                        "identity — name, age, birthday, city, job, language, nationality | "
                        "preferences — favorite food/color/music/film/game/sport, hobbies | "
                        "projects — active projects, goals, things being built | "
                        "relationships — friends, family, partner, colleagues | "
                        "wishes — future plans, things to buy, travel dreams | "
                        "notes — habits, schedule, anything else worth remembering"
                    )
                },
                "key":   {"type": "STRING", "description": "Short snake_case key (e.g. name, favorite_food, sister_name)"},
                "value": {"type": "STRING", "description": "Concise value in English (e.g. Fatih, pizza, older sister)"},
            },
            "required": ["category", "key", "value"]
        }
    },
]

class JarvisLive:

    def __init__(self, ui: JarvisUI):
        self.ui             = ui
        self.session        = None
        self.audio_in_queue = None
        self.out_queue      = None
        self._loop          = None
        self._is_speaking   = False
        self._speaking_lock = threading.Lock()
        self.ui.on_text_command = self._on_text_command

        # Live model selection state
        # _live_model: the model that will be tried on the next connect()
        # _failed_models: models that 1008'd and should be skipped
        # _minimal_config_tried: True if we already tried the minimal config
        #                        with the current model — avoids infinite loops
        # _last_connect_succeeded: True if the previous connect() call opened
        #                          a session successfully. Used to distinguish
        #                          "1008 at connect time" (config issue → retry
        #                          with minimal config) from "1008 at runtime"
        #                          (model issue → skip directly to next model).
        self._live_model: str          = _load_live_model()
        self._failed_models: set[str]  = set()
        self._minimal_config_tried: bool = False
        self._last_connect_succeeded: bool = False

    def _on_text_command(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.ui.set_state("SPEAKING")
        elif not self.ui.muted:
            self.ui.set_state("LISTENING")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str, error: str):
        short = str(error)[:120]
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    def _build_config(self, minimal: bool = False) -> types.LiveConnectConfig:
        """Build the Live API connection config.

        Args:
            minimal: When True, drops optional features (audio transcription,
                     tools) to maximise compatibility with models that reject
                     the full config. Used as a last-resort fallback when the
                     primary config triggers 1008 policy violations.
        """
        from datetime import datetime

        memory     = load_memory()
        mem_str    = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now      = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        time_ctx = (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n\n"
        )

        parts = [time_ctx]
        if mem_str:
            parts.append(mem_str)
        parts.append(sys_prompt)

        # NOTE: `session_resumption=types.SessionResumptionConfig()` was removed.
        # Passing an empty SessionResumptionConfig requests the session-resumption
        # feature without a handle, which some preview models reject with
        # "1008 policy violation — Operation is not implemented, or supported,
        # or enabled". Only enable it explicitly when we actually want to resume.
        kwargs = dict(
            response_modalities=["AUDIO"],
            system_instruction="\n".join(parts),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name="Charon"
                    )
                )
            ),
        )

        # ── CRITICAL: disable "thinking" mode ──
        # Gemini 2.5 Flash models enable internal reasoning ("thoughts") by
        # default. The Live API in audio streaming mode cannot serialise these
        # thought parts — the server sends them anyway, then closes the
        # WebSocket with 1008 "Operation is not implemented, or supported, or
        # enabled" right after the first response.
        #
        # Setting thinking_budget=0 disables the feature entirely and prevents
        # the runtime 1008. We try the structured types first (google-genai ≥1.x)
        # and fall back to a plain dict for older SDK versions.
        try:
            kwargs["generation_config"] = types.GenerationConfig(
                thinking_config=types.ThinkingConfig(thinking_budget=0)
            )
        except (AttributeError, TypeError):
            try:
                kwargs["generation_config"] = {
                    "thinking_config": {"thinking_budget": 0}
                }
            except Exception:
                pass  # SDK too old — accept the risk of runtime 1008

        if not minimal:
            # Transcription enables text mirroring of audio in/out — useful for
            # logging and memory extraction, but not supported on all models.
            kwargs["output_audio_transcription"] = {}
            kwargs["input_audio_transcription"]  = {}
            kwargs["tools"] = [{"function_declarations": TOOL_DECLARATIONS}]

        return types.LiveConnectConfig(**kwargs)

    def _intercept_memory_open(self, name: str, args: dict) -> dict:
        """Redirect any memory-opening request to cmd_control open_memory."""
        if name == "cmd_control" and args.get("action", "").lower() == "open_memory":
            return args  # Already correct

        # Catch open_app trying to open memory
        if name == "open_app":
            app_name = args.get("app_name", "").lower()
            memory_keywords = ["memory", "mémoire", "memoire", "long term",
                               "long-term", "longterm", "long terme", "long-terme"]
            if any(kw in app_name for kw in memory_keywords):
                print("[JARVIS] 🔄 Intercepted memory request from open_app → cmd_control open_memory")
                args = {"action": "open_memory"}
                return args

        return args

    async def _execute_tool(self, fc) -> types.FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        # Intercept memory-opening requests
        args = self._intercept_memory_open(name, args)
        if args.get("action") == "open_memory" and name != "cmd_control":
            name = "cmd_control"

        print(f"[JARVIS] 🔧 {name}  {args}")
        self.ui.set_state("THINKING")
        if name == "save_memory":
            category = args.get("category", "notes")
            key      = args.get("key", "")
            value    = args.get("value", "")
            if key and value:
                update_memory({category: {key: {"value": value}}})
                print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")
            if not self.ui.muted:
                self.ui.set_state("LISTENING")
            return types.FunctionResponse(
                id=fc.id, name=name,
                response={"result": "ok", "silent": True}
            )

        loop   = asyncio.get_event_loop()
        result = "Done."

        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "close_app":
                r = await loop.run_in_executor(None, lambda: close_app(parameters=args, response=None, player=self.ui))
                result = r or f"Closed {args.get('app_name')}."

            elif name == "list_open_apps":
                r = await loop.run_in_executor(None, lambda: list_open_apps(parameters=args, response=None, player=self.ui))
                result = r or "Apps listed."

            elif name == "media_control":
                r = await loop.run_in_executor(None, lambda: media_control(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."


            elif name == "screen_process":
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True
                ).start()
                result = "Vision module activated. Stay completely silent — vision module will speak directly."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id  = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.speak)
                result   = f"Task started (ID: {task_id})."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "auto_click":
                r = await loop.run_in_executor(None, lambda: auto_click(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "cmd_control":
                r = await loop.run_in_executor(None, lambda: cmd_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "discord_control":
                r = await loop.run_in_executor(None, lambda: discord_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")

                def _shutdown():
                    import time, sys, os
                    time.sleep(1)
                    os._exit(0)

                threading.Thread(target=_shutdown, daemon=True).start()
            else:
                result = f"Unknown tool: {name}"

        except Exception as e:
            result = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.speak_error(name, e)

        if not self.ui.muted:
            self.ui.set_state("LISTENING")

        print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")

        return types.FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[JARVIS] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                jarvis_speaking = self._is_speaking
            if not jarvis_speaking and not self.ui.muted:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )
        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[JARVIS] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.1)
        except Exception as e:
            print(f"[JARVIS] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[JARVIS] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        self.audio_in_queue.put_nowait(response.data)

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            self.set_speaking(True)
                            txt = sc.output_transcription.text.strip()
                            if txt:
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = sc.input_transcription.text.strip()
                            if txt:
                                in_buf.append(txt)

                        if sc.turn_complete:
                            self.set_speaking(False)

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"Jarvis: {full_out}")
                            out_buf = []

                            if full_in and len(full_in) > 5:
                                threading.Thread(
                                    target=_update_memory_async,
                                    args=(full_in, full_out),
                                    daemon=True
                                ).start()

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[JARVIS] 📞 {fc.name}")
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )

        except Exception as e:
            err_str = str(e)
            print(f"[JARVIS] ❌ Recv: {e}")
            if "1011" in err_str:
                print("[JARVIS] 🔴 Gemini 1011 — connection dropped, will reconnect")
            else:
                traceback.print_exc()
            # Do NOT re-raise — let the run() loop handle reconnection gracefully
            # The TaskGroup will still see the task ended, but without raising ExceptionGroup

    async def _play_audio(self):
        print("[JARVIS] 🔊 Play started")
        loop = asyncio.get_event_loop()

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()
        try:
            while True:
                chunk = await self.audio_in_queue.get()
                self.set_speaking(True)
                await asyncio.to_thread(stream.write, chunk)
        except Exception as e:
            print(f"[JARVIS] ❌ Play: {e}")
            raise
        finally:
            self.set_speaking(False)
            stream.stop()
            stream.close()

    def _pick_next_model(self) -> str | None:
        """Pick the next model to try, skipping failed ones.

        Iterates over LIVE_MODEL_FALLBACKS (which includes the user-configured
        model first via _load_live_model when it's also the default). Returns
        None if every model in the chain has been marked failed.
        """
        # Build an ordered candidate list starting with the configured model,
        # then appending any fallbacks not already in the list.
        candidates = [self._live_model] + [
            m for m in LIVE_MODEL_FALLBACKS if m != self._live_model
        ]
        for m in candidates:
            if m not in self._failed_models:
                return m
        return None

    async def run(self):
        client = genai.Client(
            api_key=_get_api_key(),
            http_options={"api_version": "v1beta"}
        )

        reconnect_delay = 3  # Start with 3s, increase on repeated failures
        max_delay = 30

        while True:
            # Pick the model to try this round
            model = self._pick_next_model()
            if model is None:
                # Every model has failed — reset and wait longer before retrying
                print("[JARVIS] 🔴 All Live models exhausted. Resetting failed list and waiting 60s.")
                self._failed_models.clear()
                self._minimal_config_tried = False
                self.ui.set_state("THINKING")
                await asyncio.sleep(60)
                continue

            # Decide whether to use minimal config: only when the current model
            # has already failed once with the full config (i.e. it's in the
            # failed list) AND we haven't tried minimal yet for this round.
            use_minimal = self._minimal_config_tried

            try:
                cfg_kind = "minimal" if use_minimal else "full"
                print(f"[JARVIS] 🔌 Connecting with model={model} config={cfg_kind}")
                self.ui.set_state("THINKING")
                config = self._build_config(minimal=use_minimal)

                async with (
                    client.aio.live.connect(model=model, config=config) as session,
                    asyncio.TaskGroup() as tg,
                ):
                    self.session        = session
                    self._loop          = asyncio.get_event_loop()
                    self.audio_in_queue = asyncio.Queue()
                    self.out_queue      = asyncio.Queue(maxsize=10)

                    # Mark that the connection itself succeeded — distinguishes
                    # runtime 1008 (model/feature issue mid-session) from
                    # connection-time 1008 (config rejected at handshake).
                    self._last_connect_succeeded = True

                    print(f"[JARVIS] ✅ Connected (model={model}, config={cfg_kind}).")
                    self.ui.set_state("LISTENING")
                    self.ui.write_log("SYS: JARVIS online.")
                    reconnect_delay = 3  # Reset on successful connection

                    # Sticky success — remember the working model for next reconnect
                    self._live_model = model
                    self._minimal_config_tried = False
                    self._failed_models.discard(model)

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())

            except BaseException as e:
                err_str = str(e)

                # Extract sub-exceptions from TaskGroup if present
                sub_errors = [str(sub) for sub in e.exceptions] if isinstance(e, ExceptionGroup) else []
                all_errs = " | ".join([err_str] + sub_errors)

                if isinstance(e, ExceptionGroup):
                    for sub in e.exceptions:
                        print(f"[JARVIS] ⚠️ TaskGroup sub-error: {sub}")
                else:
                    print(f"[JARVIS] ⚠️ {e}")
                    traceback.print_exc()

                runtime_failure = self._last_connect_succeeded

                # ── 1008 policy violation — model rejected or unsupported feature ──
                # Three-pronged recovery, depending on WHEN the 1008 occurred:
                #   1. Connection-time 1008 + full config → retry same model with minimal config
                #      (maybe transcription/tools were the culprit).
                #   2. Connection-time 1008 + minimal config → mark model as failed,
                #      move on to the next fallback.
                #   3. Runtime 1008 (after a successful connect) → the model itself
                #      is buggy in streaming mode (typical of preview models that
                #      emit "thoughts" the Live API can't serialise). Skip minimal
                #      config retry and go straight to the next model.
                if "1008" in all_errs:
                    print("[JARVIS] 🔴 1008 policy violation — model or feature rejected by Gemini Live API")
                    if runtime_failure:
                        # Runtime 1008 → don't bother with minimal config, the model
                        # is fundamentally incompatible with streaming. Move on.
                        print(f"[JARVIS]    Runtime 1008 (after successful connect) — marking '{model}' as failed.")
                        self._failed_models.add(model)
                        self._minimal_config_tried = False
                        reconnect_delay = 2
                    elif not use_minimal:
                        # Connection-time 1008 with full config → try minimal config
                        self._minimal_config_tried = True
                        print(f"[JARVIS]    Retrying {model} with minimal config (no transcription, no tools)...")
                        reconnect_delay = 1
                    else:
                        # Connection-time 1008 with minimal config → mark and move on
                        print(f"[JARVIS]    Marking model '{model}' as failed. Trying next fallback.")
                        self._failed_models.add(model)
                        self._minimal_config_tried = False
                        reconnect_delay = 2
                # ── 1011 internal error — server-side, just retry ──
                elif "1011" in all_errs:
                    print("[JARVIS] 🔴 Gemini API 1011 internal error — server-side issue, retrying...")
                # ── 401 / invalid API key — auth issue ──
                elif "401" in all_errs or "API key not valid" in all_errs:
                    print("[JARVIS] 🔴 Invalid Gemini API key — fix config/api_keys.json and restart Jarvis.")
                    self.ui.set_state("THINKING")
                    await asyncio.sleep(30)
                    continue
                # ── 429 / quota — rate limited, wait longer ──
                elif "429" in all_errs or "quota" in all_errs.lower():
                    print("[JARVIS] 🔴 Rate limited / quota exceeded by Gemini API")
                    reconnect_delay = max(reconnect_delay, 30)

            # Reset connect-succeeded flag at the end of each iteration
            self._last_connect_succeeded = False

            self.set_speaking(False)
            self.session = None
            self.ui.set_state("THINKING")
            print(f"[JARVIS] 🔄 Reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 1.5, max_delay)  # Exponential backoff

def main():
    import argparse
    parser = argparse.ArgumentParser(description="JARVIS — AI Assistant")
    parser.add_argument("--discord", action="store_true",
                        help="Run as Discord bot instead of voice assistant")
    args = parser.parse_args()

    if args.discord:
        print("[JARVIS] 🤖 Starting Discord bot mode...")
        from discord_bot import run_discord_bot
        asyncio.run(run_discord_bot())
        return

    ui = JarvisUI("face.png")

    def runner():
        ui.wait_for_api_key()
        jarvis = JarvisLive(ui)
        try:
            asyncio.run(jarvis.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
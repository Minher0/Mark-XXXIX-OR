"""
main.py — MARK XXXIX-OR (Local Edition)

Entry point for the JARVIS voice assistant using LOCAL models:
  - STT  : faster-whisper (local)
  - LLM  : Ollama (local)
  - TTS  : pyttsx3 (local, OS built-in voices)

The Gemini Live API has been completely removed. All other integrations
(OpenRouter, Discord, etc.) remain unchanged.

Architecture:
  Mic → VAD → Whisper STT → text
                          ↓
                      Ollama LLM (with tool declarations)
                          ↓
                  ┌─── tool_calls? ───┐
                  ↓                   ↓
              execute tools       TTS → speaker
                  ↓
          (loop back to LLM with tool results)
"""

import asyncio
import json
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from ui import JarvisUI
from memory.memory_manager import (
    load_memory, update_memory, format_memory_for_prompt,
    should_extract_memory, extract_memory
)
from local_llm import LocalLLM
from local_stt import LocalSTT
from local_tts import LocalTTS

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


def _get_api_key() -> str:
    """Kept for backward compatibility — returns empty string in local mode.

    Some legacy modules still call this. They've been migrated to local_llm,
    but we keep the function to avoid ImportErrors during the transition.
    """
    try:
        with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f).get("gemini_api_key", "")
    except Exception:
        return ""


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are JARVIS, Tony Stark's AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )


# ─── Tool declarations (kept identical to Gemini version) ───
# These are now consumed by local_llm.py which converts them to the
# OpenAI-compatible format expected by Ollama.

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
            "For SYSTEM-WIDE volume, use computer_settings volume_set instead. "
            "For per-app volume targeting a SPECIFIC app by name, use computer_settings set_app_volume."
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


# ─── JarvisLocal: the main voice assistant loop ─────────────

class JarvisLocal:
    """Voice assistant using local STT + LLM + TTS.

    The loop:
      1. Listen for speech via VAD + Whisper STT
      2. Send transcript to Ollama with conversation history + tools
      3. If tool_calls → execute them → feed results back → goto 2
      4. If text response → speak via TTS → goto 1

    Barge-in: when TTS is playing, STT is paused. When speech is detected
    during TTS, we stop TTS and start processing the new utterance.
    """

    MAX_TURNS = 20         # conversation history limit
    MAX_TOOL_ROUNDS = 8    # safety: avoid infinite tool loops

    def __init__(self, ui: JarvisUI):
        self.ui = ui
        self.llm = LocalLLM()
        self.stt = LocalSTT()
        self.tts = LocalTTS()
        self.conversation: list = []   # [{role, content}, ...]
        self._running = False
        self._processing = False  # True while LLM is thinking / tool executing
        self.ui.on_text_command = self._on_text_command

    def _build_system_prompt(self) -> str:
        """Build the system prompt with current date/time + memory."""
        memory = load_memory()
        mem_str = format_memory_for_prompt(memory)
        sys_prompt = _load_system_prompt()

        now = datetime.now()
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
        return "\n".join(parts)

    def _on_text_command(self, text: str):
        """Handle text input from the UI (instead of voice)."""
        if not text.strip():
            return
        threading.Thread(
            target=self._process_user_input,
            args=(text,),
            daemon=True,
        ).start()

    def _on_transcript(self, text: str):
        """Called by STT when speech is transcribed."""
        if not text or not text.strip():
            return
        if self._processing:
            # Barge-in: user is interrupting
            print(f"[JARVIS] ⏸️ Barge-in: '{text[:50]}'")
            self.tts.stop_speaking()
        # Process in a separate thread so STT can continue listening
        threading.Thread(
            target=self._process_user_input,
            args=(text,),
            daemon=True,
        ).start()

    def _process_user_input(self, user_text: str):
        """Process a user utterance: send to LLM, handle tools, speak response."""
        if self._processing:
            return  # already processing — ignore (barge-in case handled above)
        self._processing = True
        self.ui.set_state("THINKING")
        self.ui.write_log(f"You: {user_text}")
        self.tts.pause()  # pause STT during processing
        # Stop TTS in case it's still going
        self.tts.stop_speaking()

        try:
            # Add user message to history
            self.conversation.append({"role": "user", "content": user_text})
            self._trim_history()

            # Run the LLM + tool loop
            self._run_llm_tool_loop()

        except Exception as e:
            print(f"[JARVIS] ❌ Processing error: {e}")
            traceback.print_exc()
            self.ui.write_log(f"ERR: {e}")
            self.tts.speak(f"Sir, an error occurred. {str(e)[:120]}")
        finally:
            self._processing = False
            self.tts.resume()
            if not self.tts.is_speaking:
                self.ui.set_state("LISTENING")

    def _run_llm_tool_loop(self):
        """Call the LLM, execute any tool calls, feed results back, repeat."""
        system_prompt = self._build_system_prompt()

        # Build the messages list (system + history)
        messages = [{"role": "system", "content": system_prompt}] + self.conversation

        for round_num in range(self.MAX_TOOL_ROUNDS):
            print(f"[JARVIS] 🧠 LLM round {round_num + 1}...")
            response = self.llm.chat_with_tools(
                messages=messages,
                tools=TOOL_DECLARATIONS,
                temperature=0.7,
                max_tokens=2048,
            )

            content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])

            # If the LLM produced text, append it to history + speak
            if content:
                self.conversation.append({"role": "assistant", "content": content})
                self._trim_history()
                print(f"[JARVIS] 🗣️ {content[:200]}")
                self.ui.write_log(f"Jarvis: {content}")
                # Speak in a separate thread so we can detect barge-in
                self.ui.set_state("SPEAKING")
                self.tts.speak(content)
                # Wait for TTS to finish (with timeout in case it hangs)
                self._wait_for_tts()

            # Handle save_memory silently (don't speak)
            silent_tools = {"save_memory", "shutdown_jarvis"}
            spoken_tools = [tc for tc in tool_calls if tc["name"] not in silent_tools]

            # If no tool calls, we're done
            if not tool_calls:
                break

            # Execute tool calls
            # Add the assistant's tool-calling message to history
            messages.append({
                "role": "assistant",
                "content": content or "",
                "tool_calls": [
                    {"id": f"call_{i}", "type": "function",
                     "function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for i, tc in enumerate(tool_calls)
                ],
            })

            for tc in tool_calls:
                name = tc["name"]
                args = tc["arguments"]
                print(f"[JARVIS] 🔧 {name}  {args}")
                self.ui.write_log(f"[tool] {name}")

                # Special: shutdown
                if name == "shutdown_jarvis":
                    self.tts.speak("Goodbye, sir.")
                    self._wait_for_tzs()
                    self._running = False
                    # Schedule shutdown
                    threading.Thread(target=self._shutdown, daemon=True).start()
                    return

                # Special: save_memory (silent, no history)
                if name == "save_memory":
                    self._handle_save_memory(args)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": f"call_{tool_calls.index(tc)}",
                        "content": "ok",
                    })
                    continue

                # Execute the tool
                result = self._execute_tool(name, args)
                print(f"[JARVIS] 📤 {name} → {str(result)[:80]}")
                messages.append({
                    "role": "tool",
                    "tool_call_id": f"call_{tool_calls.index(tc)}",
                    "content": str(result),
                })

            # Loop back to call the LLM again with tool results
            # (don't speak until we have a final text response)
        else:
            print(f"[JARVIS] ⚠️ Hit MAX_TOOL_ROUNDS ({self.MAX_TOOL_ROUNDS})")
            self.tts.speak("Sir, I've reached the maximum number of tool calls for this turn.")

    def _wait_for_tts(self):
        """Wait for TTS to finish speaking (with timeout)."""
        deadline = time.time() + 120  # 2 min max per response
        while self.tts.is_speaking and time.time() < deadline:
            time.sleep(0.1)

    def _wait_for_tzs(self):
        """Alias for _wait_for_tts (typo guard)."""
        self._wait_for_tts()

    def _trim_history(self):
        """Keep conversation history under MAX_TURNS."""
        if len(self.conversation) > self.MAX_TURNS:
            # Keep the last MAX_TURNS messages
            self.conversation = self.conversation[-self.MAX_TURNS:]

    def _handle_save_memory(self, args: dict):
        """Silently save a memory entry."""
        category = args.get("category", "notes")
        key = args.get("key", "")
        value = args.get("value", "")
        if key and value:
            update_memory({category: {key: {"value": value}}})
            print(f"[Memory] 💾 save_memory: {category}/{key} = {value}")

    def _execute_tool(self, name: str, args: dict) -> str:
        """Execute a single tool call. Returns the result as a string."""
        try:
            # Use a synchronous executor — Ollama calls are blocking already
            if name == "open_app":
                return open_app(parameters=args, response=None, player=self.ui) or f"Opened {args.get('app_name')}."

            if name == "close_app":
                return close_app(parameters=args, response=None, player=self.ui) or f"Closed {args.get('app_name')}."

            if name == "list_open_apps":
                return list_open_apps(parameters=args, response=None, player=self.ui) or "Apps listed."

            if name == "media_control":
                return media_control(parameters=args, response=None, player=self.ui) or "Done."

            if name == "weather_report":
                return weather_action(parameters=args, player=self.ui) or "Weather delivered."

            if name == "browser_control":
                return browser_control(parameters=args, player=self.ui) or "Done."

            if name == "file_controller":
                return file_controller(parameters=args, player=self.ui) or "Done."

            if name == "send_message":
                return send_message(parameters=args, response=None, player=self.ui, session_memory=None) or f"Message sent to {args.get('receiver')}."

            if name == "reminder":
                return reminder(parameters=args, response=None, player=self.ui) or "Reminder set."

            if name == "youtube_video":
                return youtube_video(parameters=args, response=None, player=self.ui) or "Done."

            if name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                return file_processor(parameters=args, player=self.ui, speak=self.tts.speak) or "Done."

            if name == "screen_process":
                # Launch in a separate thread — it has its own loop
                threading.Thread(
                    target=screen_process,
                    kwargs={"parameters": args, "response": None,
                            "player": self.ui, "session_memory": None},
                    daemon=True,
                ).start()
                return "Vision module activated."

            if name == "computer_settings":
                return computer_settings(parameters=args, response=None, player=self.ui) or "Done."

            if name == "desktop_control":
                return desktop_control(parameters=args, player=self.ui) or "Done."

            if name == "code_helper":
                return code_helper(parameters=args, player=self.ui, speak=self.tts.speak) or "Done."

            if name == "dev_agent":
                return dev_agent(parameters=args, player=self.ui, speak=self.tts.speak) or "Done."

            if name == "agent_task":
                from agent.task_queue import get_queue, TaskPriority
                priority_map = {"low": TaskPriority.LOW, "normal": TaskPriority.NORMAL, "high": TaskPriority.HIGH}
                priority = priority_map.get(args.get("priority", "normal").lower(), TaskPriority.NORMAL)
                task_id = get_queue().submit(goal=args.get("goal", ""), priority=priority, speak=self.tts.speak)
                return f"Task started (ID: {task_id})."

            if name == "web_search":
                return web_search_action(parameters=args, player=self.ui) or "Done."

            if name == "computer_control":
                return computer_control(parameters=args, player=self.ui) or "Done."

            if name == "auto_click":
                return auto_click(parameters=args, player=self.ui) or "Done."

            if name == "cmd_control":
                return cmd_control(parameters=args, player=self.ui) or "Done."

            if name == "game_updater":
                return game_updater(parameters=args, player=self.ui, speak=self.tts.speak) or "Done."

            if name == "flight_finder":
                return flight_finder(parameters=args, player=self.ui) or "Done."

            if name == "discord_control":
                return discord_control(parameters=args, player=self.ui) or "Done."

            return f"Unknown tool: {name}"

        except Exception as e:
            err = f"Tool '{name}' failed: {e}"
            traceback.print_exc()
            self.ui.write_log(f"ERR: {name} — {str(e)[:120]}")
            return err

    def _shutdown(self):
        """Force-shutdown Jarvis after a short delay."""
        import os
        time.sleep(1.5)
        os._exit(0)

    def run(self):
        """Start the assistant: init TTS/STT, listen forever."""
        print("[JARVIS] 🚀 Starting local voice assistant...")
        self._running = True

        # Init TTS
        try:
            self.tts.on_speak_start = lambda: self.ui.set_state("SPEAKING")
            self.tts.on_speak_end = lambda: self.ui.set_state("LISTENING") if self._running else None
            self.tts.start()
        except Exception as e:
            print(f"[JARVIS] ❌ TTS init failed: {e}")
            self.ui.write_log(f"SYS: TTS error — {e}")

        # Init STT
        try:
            self.stt.on_transcript = self._on_transcript
            self.stt.on_state_change = lambda s: self.ui.set_state(
                {"idle": "THINKING", "listening": "LISTENING",
                 "speaking_detected": "LISTENING", "transcribing": "THINKING"}.get(s, "LISTENING")
            )
            self.stt.start()
        except Exception as e:
            print(f"[JARVIS] ❌ STT init failed: {e}")
            self.ui.write_log(f"SYS: STT error — {e}")

        # Welcome
        self.ui.set_state("LISTENING")
        self.ui.write_log("SYS: JARVIS online (local mode).")
        self.tts.speak("JARVIS online, sir. Local models ready.")

        # Main loop: just keep the thread alive
        try:
            while self._running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")
        finally:
            self._running = False
            self.stt.stop()
            self.tts.stop()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="JARVIS — AI Assistant (Local)")
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
        jarvis = JarvisLocal(ui)
        try:
            jarvis.run()
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()

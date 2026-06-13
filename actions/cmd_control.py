#cmd_control.py
import json
import subprocess
import sys
import platform
import os
from pathlib import Path

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
    return _load_config().get("os_system", platform.system().lower()).lower()

_BLOCKED_SUBSTRINGS = [
    "format ", "del /", "rmdir /s", "rd /s",
    "rm -rf /", "rm -rf /*", ":(){ :|:& };:",
    "mkfs.", "dd if=", "> /dev/sd",
    "shutdown", "reboot", "poweroff",
    "reg delete", "reg add",
    "net user", "net localgroup",
    "cipher /w",
]

_BLOCKED_EXACT = {
    "format c:", "format d:",
    "del /s /q c:\\", "del /s /q c:/",
    "rm -rf /", "rm -rf /*", "rm -rf ~",
    "chmod -r 777 /",
    "> /dev/sda",
}


def _is_dangerous(command: str) -> str | None:
    cmd_lower = command.strip().lower()

    if cmd_lower in _BLOCKED_EXACT:
        return f"Blocked dangerous command: '{command}'"

    for substr in _BLOCKED_SUBSTRINGS:
        if substr in cmd_lower:
            return f"Blocked command contains dangerous pattern: '{substr}'"

    return None


def _run_command(command: str, timeout: int = 30) -> str:
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout.strip()
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr.strip()
        if not output:
            output = "Command executed (no output)."
        return output

    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except FileNotFoundError:
        return f"Command not found: {command.split()[0]}"
    except Exception as e:
        return f"Execution error: {e}"


def _run_in_dir(command: str, working_dir: str, timeout: int = 30) -> str:
    if not os.path.isdir(working_dir):
        return f"Directory not found: {working_dir}"
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=working_dir,
        )
        output = ""
        if result.stdout:
            output += result.stdout.strip()
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr.strip()
        if not output:
            output = "Command executed (no output)."
        return output

    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except Exception as e:
        return f"Execution error: {e}"


def _run_piped(command: str, timeout: int = 30) -> str:
    try:
        if _get_os() == "windows":
            cmd = ["cmd", "/c", command]
        else:
            cmd = ["bash", "-c", command]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = ""
        if result.stdout:
            output += result.stdout.strip()
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr.strip()
        if not output:
            output = "Command executed (no output)."
        return output

    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s."
    except Exception as e:
        return f"Execution error: {e}"


def _run_background(command: str) -> str:
    try:
        if _get_os() == "windows":
            subprocess.Popen(
                command,
                shell=True,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                command,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        return f"Background process started: {command}"

    except Exception as e:
        return f"Failed to start background process: {e}"


def _list_processes(filter_name: str = "") -> str:
    try:
        if _get_os() == "windows":
            cmd = 'tasklist /fo csv /nh'
            if filter_name:
                cmd += f' /fi "imagename eq {filter_name}*"'
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
        else:
            if filter_name:
                result = subprocess.run(
                    ["ps", "aux"], capture_output=True, text=True, timeout=10
                )
                lines   = result.stdout.splitlines()
                header  = lines[0] if lines else ""
                filtered = [header] + [l for l in lines[1:] if filter_name.lower() in l.lower()]
                result_stdout = "\n".join(filtered[:30])
                if len(filtered) > 30:
                    result_stdout += f"\n... and {len(filtered) - 30} more."
                return result_stdout if result_stdout else f"No processes matching '{filter_name}'."
            else:
                result = subprocess.run(
                    ["ps", "aux"], capture_output=True, text=True, timeout=10
                )

        output = result.stdout.strip() if result.stdout else "No processes found."
        lines  = output.splitlines()
        if len(lines) > 30:
            output = "\n".join(lines[:30]) + f"\n... and {len(lines) - 30} more."
        return output

    except Exception as e:
        return f"Failed to list processes: {e}"


def _kill_process(pid_or_name: str) -> str:
    try:
        try:
            pid = int(pid_or_name)
            if _get_os() == "windows":
                result = subprocess.run(
                    ["taskkill", "/pid", str(pid), "/f"],
                    capture_output=True, text=True, timeout=5
                )
            else:
                result = subprocess.run(
                    ["kill", "-9", str(pid)],
                    capture_output=True, text=True, timeout=5
                )
            if result.returncode == 0:
                return f"Process {pid} terminated."
            return f"Could not kill process {pid}: {result.stderr.strip()}"
        except ValueError:
            pass

        name = pid_or_name
        if _get_os() == "windows":
            result = subprocess.run(
                ["taskkill", "/im", name, "/f"],
                capture_output=True, text=True, timeout=5
            )
        else:
            result = subprocess.run(
                ["pkill", "-9", name],
                capture_output=True, text=True, timeout=5
            )
        if result.returncode == 0:
            return f"Process(es) '{name}' terminated."
        return f"Could not kill '{name}': {result.stderr.strip()}"

    except Exception as e:
        return f"Failed to kill process: {e}"


def _network_info() -> str:
    try:
        if _get_os() == "windows":
            result = subprocess.run(
                ["ipconfig"], capture_output=True, text=True, timeout=10
            )
        else:
            result = subprocess.run(
                ["ip", "addr", "show"] if os.path.exists("/sbin/ip") else ["ifconfig"],
                capture_output=True, text=True, timeout=10
            )
        return result.stdout.strip() if result.stdout else "No network info available."

    except Exception as e:
        return f"Failed to get network info: {e}"


def _disk_usage() -> str:
    try:
        if _get_os() == "windows":
            result = subprocess.run(
                ["wmic", "logicaldisk", "get", "size,freespace,caption"],
                capture_output=True, text=True, timeout=10
            )
        else:
            result = subprocess.run(
                ["df", "-h"],
                capture_output=True, text=True, timeout=10
            )
        return result.stdout.strip() if result.stdout else "No disk info available."

    except Exception as e:
        return f"Failed to get disk usage: {e}"


def _system_info() -> str:
    info = []
    info.append(f"OS: {platform.system()} {platform.release()} ({platform.machine()})")
    info.append(f"Hostname: {platform.node()}")
    info.append(f"Python: {platform.python_version()}")

    try:
        if _get_os() == "windows":
            result = subprocess.run(
                ["systeminfo"],
                capture_output=True, text=True, timeout=15
            )
            lines = result.stdout.strip().splitlines()[:12]
            info.extend(lines)
        else:
            if os.path.exists("/usr/bin/uname"):
                result = subprocess.run(
                    ["uname", "-a"],
                    capture_output=True, text=True, timeout=5
                )
                info.append(result.stdout.strip())
            if os.path.exists("/proc/uptime"):
                with open("/proc/uptime") as f:
                    uptime_secs = float(f.read().split()[0])
                    hours = int(uptime_secs // 3600)
                    mins  = int((uptime_secs % 3600) // 60)
                    info.append(f"Uptime: {hours}h {mins}m")
    except Exception:
        pass

    return "\n".join(info)


def cmd_control(
    parameters: dict = None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Dispatch table for terminal / CMD command execution.

    parameters keys (all optional unless noted):
      action        : (required) one of the actions listed below
      command       : the command string to execute
      working_dir   : directory to run the command in (for run_in_dir)
      timeout       : execution timeout in seconds (default: 30, max: 120)
      filter        : process name filter (for list_processes)
      process       : PID or process name (for kill_process)

    Actions:
      run            — execute a single command and return output
      run_in_dir     — execute a command in a specific working directory
      run_piped      — execute piped / chained commands (|, &&, ;)
      run_background — start a command in the background (non-blocking)
      list_processes — list running processes (optional filter)
      kill_process   — kill a process by PID or name
      network_info   — show network configuration
      disk_usage     — show disk space information
      system_info    — show system/OS information
    """
    params  = parameters or {}
    action  = params.get("action", "").lower().strip()
    command = params.get("command", "").strip()

    if not action:
        return "No action specified for cmd_control."

    if player:
        player.write_log(f"[CMD] {action}: {command[:50]}")

    print(f"[CmdControl] ▶ {action}  {params}")

    try:

        if action == "run":
            if not command:
                return "No command provided."
            danger = _is_dangerous(command)
            if danger:
                return f"SAFETY: {danger} If you really need to do this, please do it manually."
            timeout = min(int(params.get("timeout", 30)), 120)
            return _run_command(command, timeout=timeout)

        if action == "run_in_dir":
            if not command:
                return "No command provided."
            working_dir = params.get("working_dir", "").strip()
            if not working_dir:
                return "No working directory specified for run_in_dir."
            danger = _is_dangerous(command)
            if danger:
                return f"SAFETY: {danger}"
            timeout = min(int(params.get("timeout", 30)), 120)
            return _run_in_dir(command, working_dir, timeout=timeout)

        if action == "run_piped":
            if not command:
                return "No command provided."
            danger = _is_dangerous(command)
            if danger:
                return f"SAFETY: {danger}"
            timeout = min(int(params.get("timeout", 30)), 120)
            return _run_piped(command, timeout=timeout)

        if action == "run_background":
            if not command:
                return "No command provided."
            danger = _is_dangerous(command)
            if danger:
                return f"SAFETY: {danger}"
            return _run_background(command)

        if action == "list_processes":
            filter_name = params.get("filter", params.get("process", "")).strip()
            return _list_processes(filter_name)

        if action == "kill_process":
            proc = params.get("process", params.get("pid", "")).strip()
            if not proc:
                return "No process name or PID specified."
            return _kill_process(proc)

        if action == "network_info":
            return _network_info()

        if action == "disk_usage":
            return _disk_usage()

        if action == "system_info":
            return _system_info()

        return f"Unknown action: '{action}'"

    except Exception as e:
        print(f"[CmdControl] ❌ {action}: {e}")
        return f"cmd_control '{action}' failed: {e}"

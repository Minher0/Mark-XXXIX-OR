#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║  JARVIS — Auto-Updater                              ║
║                                                      ║
║  Pulls the latest changes from GitHub, updates       ║
║  dependencies, and restarts Jarvis.                  ║
║                                                      ║
║  Uses git pull if git is available.                  ║
║  Falls back to downloading ZIP from GitHub.          ║
╚══════════════════════════════════════════════════════╝
"""

import os
import sys
import subprocess
import shutil
import urllib.request
import zipfile
import io
import time
import hashlib
from pathlib import Path

# ─── Constants ───────────────────────────────────────────────

APP_NAME     = "Jarvis"
GITHUB_REPO  = "https://github.com/Minher0/Mark-XXXIX-OR.git"
GITHUB_ZIP   = "https://github.com/Minher0/Mark-XXXIX-OR/archive/refs/heads/main.zip"

LOCAL_APP    = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
APP_DIR      = LOCAL_APP / APP_NAME
REPO_DIR     = APP_DIR / "app"
VENV_DIR     = APP_DIR / "venv"
REQUIREMENTS = REPO_DIR / "requirements.txt"

VENV_PYTHON  = VENV_DIR / "Scripts" / "python.exe"


# ─── Console helpers ─────────────────────────────────────────

def _bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"

def _green(text: str) -> str:
    return f"\033[92m{text}\033[0m"

def _red(text: str) -> str:
    return f"\033[91m{text}\033[0m"

def _yellow(text: str) -> str:
    return f"\033[93m{text}\033[0m"

def _cyan(text: str) -> str:
    return f"\033[96m{text}\033[0m"

def _step(n: int, total: int, msg: str) -> None:
    print(f"  {_cyan(f'[{n}/{total}]')} {msg}", end=" ", flush=True)

def _ok(msg: str = "OK") -> None:
    print(_green(f"✓ {msg}"))

def _fail(msg: str = "FAILED") -> None:
    print(_red(f"✗ {msg}"))


# ─── Helpers ─────────────────────────────────────────────────

def _file_hash(path: Path) -> str:
    """Get MD5 hash of a file for change detection."""
    if not path.exists():
        return ""
    return hashlib.md5(path.read_bytes()).hexdigest()


def _is_jarvis_running() -> bool:
    """Check if Jarvis is currently running."""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe"],
            capture_output=True, text=True, timeout=5
        )
        # Check if main.py is in the command line
        result2 = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "commandline"],
            capture_output=True, text=True, timeout=5
        )
        if "main.py" in result2.stdout:
            return True
    except Exception:
        pass
    return False


def _kill_jarvis() -> bool:
    """Kill any running Jarvis process."""
    try:
        # Find PIDs of python processes running main.py
        result = subprocess.run(
            ["wmic", "process", "where", "name='python.exe'", "get", "processid,commandline"],
            capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.splitlines():
            if "main.py" in line:
                parts = line.strip().split()
                pid = parts[-1] if parts else None
                if pid and pid.isdigit():
                    subprocess.run(["taskkill", "/F", "/PID", pid], capture_output=True, timeout=5)
                    _ok(f"Killed Jarvis (PID {pid})")
                    time.sleep(1)
                    return True
    except Exception:
        pass

    # Fallback: just try killing python processes in the repo directory
    try:
        subprocess.run(
            ["taskkill", "/F", "/FI", f"WORKINGDIR eq {REPO_DIR}"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass
    return False


# ─── Update steps ────────────────────────────────────────────

def check_git() -> str | None:
    """Check if git is available."""
    try:
        result = subprocess.run(
            ["git", "--version"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return "git"
    except Exception:
        pass
    return None


def git_pull() -> bool:
    """Pull latest changes from GitHub using git."""
    try:
        # Check if the repo is a git repo
        git_dir = REPO_DIR / ".git"
        if not git_dir.exists():
            print("\n      Not a git repo. Initializing...")
            subprocess.run(
                ["git", "init"],
                cwd=str(REPO_DIR), capture_output=True, timeout=10
            )
            subprocess.run(
                ["git", "remote", "add", "origin", GITHUB_REPO],
                cwd=str(REPO_DIR), capture_output=True, timeout=10
            )
            subprocess.run(
                ["git", "fetch", "origin"],
                cwd=str(REPO_DIR), capture_output=True, timeout=30
            )
            subprocess.run(
                ["git", "reset", "--hard", "origin/main"],
                cwd=str(REPO_DIR), capture_output=True, timeout=10
            )
            _ok("Initialized and fetched")
            return True

        # Normal git pull
        # Stash any local changes first
        subprocess.run(
            ["git", "stash"],
            cwd=str(REPO_DIR), capture_output=True, timeout=10
        )

        result = subprocess.run(
            ["git", "pull", "origin", "main"],
            cwd=str(REPO_DIR),
            capture_output=True, text=True, timeout=60
        )

        # Restore stashed changes (if any)
        stash_result = subprocess.run(
            ["git", "stash", "pop"],
            cwd=str(REPO_DIR), capture_output=True, timeout=10
        )

        if result.returncode == 0:
            # Check if anything actually changed
            if "Already up to date" in result.stdout:
                _ok("Already up to date")
                return False  # No changes
            else:
                _ok("Updated successfully")
                return True  # Changes were pulled
        else:
            _warn(f"git pull failed: {result.stderr.strip()[:100]}")
            return False

    except subprocess.TimeoutExpired:
        _warn("git pull timed out")
        return False
    except Exception as e:
        _warn(f"git error: {e}")
        return False


def download_zip_update() -> bool:
    """Fallback: download ZIP from GitHub and extract."""
    print("\n      Downloading latest version from GitHub...")
    try:
        req = urllib.request.Request(GITHUB_ZIP, headers={
            "User-Agent": "Jarvis-Updater/1.0"
        })
        with urllib.request.urlopen(req, timeout=60) as response:
            zip_data = response.read()

        extract_dir = APP_DIR / "_temp_update"
        if extract_dir.exists():
            shutil.rmtree(str(extract_dir))

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            zf.extractall(str(extract_dir))

        extracted = list(extract_dir.iterdir())
        if extracted:
            src = extracted[0]
            # Copy over the existing files (preserve config and memory)
            for item in src.iterdir():
                dst = REPO_DIR / item.name
                # Preserve user data
                if item.name in ("config", "memory"):
                    # Only copy new/changed files, don't overwrite user data
                    if item.is_dir():
                        for sub in item.rglob("*"):
                            if sub.is_file():
                                rel = sub.relative_to(item)
                                target = dst / rel
                                if not target.exists():
                                    target.parent.mkdir(parents=True, exist_ok=True)
                                    shutil.copy2(str(sub), str(target))
                else:
                    # Overwrite everything else
                    if dst.exists():
                        if dst.is_dir():
                            shutil.rmtree(str(dst))
                        else:
                            dst.unlink()
                    shutil.move(str(item), str(dst))

            shutil.rmtree(str(extract_dir), ignore_errors=True)

        _ok("Downloaded and updated")
        return True

    except Exception as e:
        _fail(f"Download failed: {e}")
        return False


def update_deps() -> bool:
    """Update pip dependencies if requirements.txt changed."""
    if not REQUIREMENTS.exists() or not VENV_PYTHON.exists():
        _warn("Cannot update deps (missing requirements or venv)")
        return False

    print("\n      Checking dependencies...")
    try:
        process = subprocess.Popen(
            [str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS), "--quiet", "--upgrade"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        for line in process.stdout:
            line = line.strip()
            if line and "already satisfied" not in line.lower():
                print(f"        {line}")

        process.wait()
        if process.returncode == 0:
            _ok("Dependencies up to date")
            return True
        _warn("Some dependencies failed to update")
        return True
    except Exception as e:
        _fail(str(e))
        return False


def restart_jarvis() -> None:
    """Restart Jarvis after update."""
    print()
    print(_bold("  Restarting Jarvis..."))
    print()

    try:
        os.chdir(str(REPO_DIR))
        subprocess.run([str(VENV_PYTHON), str(REPO_DIR / "main.py")])
    except KeyboardInterrupt:
        print("\n  Jarvis shutting down...")
    except Exception as e:
        print(_red(f"\n  Error launching Jarvis: {e}"))
        input("\n  Press Enter to exit...")


# ─── Main ────────────────────────────────────────────────────

def main():
    print()
    print(_bold("  ╔════════════════════════════════════════════════╗"))
    print(_bold("  ║   JARVIS — Auto-Updater                        ║"))
    print(_bold("  ╚════════════════════════════════════════════════╝"))
    print()

    # Check if Jarvis is installed
    if not (REPO_DIR / "main.py").exists():
        _fail("Jarvis is not installed. Run Jarvis.exe first.")
        input("\n  Press Enter to exit...")
        sys.exit(1)

    TOTAL_STEPS = 4

    # Step 1: Check if Jarvis is running
    _step(1, TOTAL_STEPS, "Checking Jarvis status...")
    if _is_jarvis_running():
        _ok("Running — will restart after update")
    else:
        _ok("Not running")

    # Step 2: Pull updates
    _step(2, TOTAL_STEPS, "Checking for updates...")
    git = check_git()
    updated = False

    if git:
        updated = git_pull()
    else:
        _warn("Git not available, using ZIP download")
        updated = download_zip_update()

    # Step 3: Update dependencies
    _step(3, TOTAL_STEPS, "Updating dependencies...")
    if updated:
        update_deps()
    else:
        _ok("No changes needed")

    # Step 4: Restart
    _step(4, TOTAL_STEPS, "Ready to launch...")
    _ok()

    print()
    if updated:
        print(_green(_bold("  ✓ Jarvis updated successfully!")))
    else:
        print(_green(_bold("  ✓ Jarvis is already up to date!")))
    print()

    # Ask to restart
    try:
        answer = input("  Restart Jarvis now? [Y/n] ").strip().lower()
    except EOFError:
        answer = "y"

    if answer in ("", "y", "yes", "oui", "o"):
        if _is_jarvis_running():
            _kill_jarvis()
            time.sleep(2)
        restart_jarvis()
    else:
        print(_yellow("\n  You can start Jarvis later with Jarvis.exe"))


if __name__ == "__main__":
    main()

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

def _warn(msg: str = "WARNING") -> None:
    print(_yellow(f"⚠ {msg}"))


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


# Directories that must NEVER be touched during updates
_PROTECTED_DIRS = {"memory", "config"}


def _backup_protected() -> dict[str, Path]:
    """Backup memory/ and config/ to a temp location before updating.
    Returns a dict of {name: backup_path}."""
    backups = {}
    backup_root = APP_DIR / "_update_backup"
    if backup_root.exists():
        shutil.rmtree(str(backup_root))
    backup_root.mkdir(parents=True, exist_ok=True)

    for name in _PROTECTED_DIRS:
        src = REPO_DIR / name
        if src.exists():
            dst = backup_root / name
            shutil.copytree(str(src), str(dst))
            backups[name] = dst
            print(f"      Backed up {name}/")

    return backups


def _rmtree_safe(path) -> None:
    """Delete a directory tree, retrying on PermissionError (Windows file locks)."""
    for attempt in range(5):
        try:
            shutil.rmtree(str(path))
            return
        except PermissionError:
            if attempt < 4:
                time.sleep(0.5 * (attempt + 1))
            else:
                # Last attempt: force-delete files individually
                try:
                    for root, dirs, files in os.walk(str(path), topdown=False):
                        for f in files:
                            try:
                                os.remove(os.path.join(root, f))
                            except Exception:
                                pass
                        for d in dirs:
                            try:
                                os.rmdir(os.path.join(root, d))
                            except Exception:
                                pass
                    try:
                        os.rmdir(str(path))
                    except Exception:
                        pass
                except Exception:
                    pass


def _restore_protected(backups: dict[str, Path]) -> None:
    """Restore memory/ and config/ from backup after updating."""
    for name, backup_path in backups.items():
        dst = REPO_DIR / name
        # Remove whatever git/zip put there (safe delete with retries)
        if dst.exists():
            _rmtree_safe(dst)
        # Restore our backup
        if backup_path.exists():
            shutil.copytree(str(backup_path), str(dst))
            print(f"      Restored {name}/")

    # Cleanup backup dir
    backup_root = APP_DIR / "_update_backup"
    if backup_root.exists():
        _rmtree_safe(backup_root)


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
    """Pull latest changes from GitHub using git.
    Protects memory/ and config/ from being overwritten."""
    try:
        # Backup protected dirs before pulling
        backups = _backup_protected()

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
            # Force checkout but we already backed up protected dirs
            subprocess.run(
                ["git", "checkout", "-b", "main", "origin/main"],
                cwd=str(REPO_DIR), capture_output=True, timeout=10
            )
            # Restore protected dirs
            _restore_protected(backups)
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

        # Restore protected dirs (overwrite whatever git put there)
        _restore_protected(backups)

        # Don't restore stash for memory/config (we already have our backup)
        # Only restore stash for other files like code changes user made
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
                _ok("Updated successfully (memory/ and config/ preserved)")
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
    """Fallback: download ZIP from GitHub and extract.
    Protects memory/ and config/ from being overwritten."""
    print("\n      Downloading latest version from GitHub...")

    # Backup protected dirs before any file operations
    backups = _backup_protected()

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
            # Copy over the existing files, SKIPPING protected dirs entirely
            for item in src.iterdir():
                dst = REPO_DIR / item.name
                # NEVER touch memory/ and config/
                if item.name in _PROTECTED_DIRS:
                    continue  # Skip completely
                # Overwrite everything else
                if dst.exists():
                    if dst.is_dir():
                        shutil.rmtree(str(dst))
                    else:
                        dst.unlink()
                shutil.move(str(item), str(dst))

            shutil.rmtree(str(extract_dir), ignore_errors=True)

        # Restore protected dirs (in case git/zip overwrote them)
        _restore_protected(backups)

        _ok("Downloaded and updated (memory/ and config/ preserved)")
        return True

    except Exception as e:
        # Restore even on failure
        _restore_protected(backups)
        _fail(f"Download failed: {e}")
        return False


def update_deps() -> bool:
    """Update pip dependencies if requirements.txt changed.
    Installs packages one by one so one failure doesn't block others.
    Verifies each package is actually importable after install."""
    if not REQUIREMENTS.exists() or not VENV_PYTHON.exists():
        _warn("Cannot update deps (missing requirements or venv)")
        return False

    # Read packages, skip comments and blank lines
    try:
        content = REQUIREMENTS.read_text(encoding="utf-8")
    except Exception:
        content = REQUIREMENTS.read_text()

    packages = []
    for line in content.splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            packages.append(line)

    if not packages:
        _ok("No packages to update")
        return True

    print(f"\n      Checking {len(packages)} dependencies...")

    failed = []
    succeeded = 0
    installed_now = []  # packages installed during this run (not already there)

    for pkg in packages:
        pkg_name = pkg.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
        # Normalise package name for import check
        # e.g. "google-genai" -> "google.genai", "beautifulsoup4" -> "bs4"
        import_name = {
            "beautifulsoup4": "bs4",
            "Pillow": "PIL",
            "pyautogui": "pyautogui",
            "opencv-python": "cv2",
            "duckduckgo-search": "duckduckgo_search",
            "youtube-transcript-api": "youtube_transcript_api",
            "google-genai": "google.genai",
            "google-generativeai": "google.generativeai",
            "browser-use": "browser_use",
            "langchain-google-genai": "langchain_google_genai",
            "send2trash": "send2trash",
            "youtube-transcript-api": "youtube_transcript_api",
        }.get(pkg_name, pkg_name.lower().replace("-", "_"))

        # Check if already importable
        check = subprocess.run(
            [str(VENV_PYTHON), "-c", f"import {import_name}"],
            capture_output=True, timeout=15
        )
        already_installed = (check.returncode == 0)

        if already_installed:
            succeeded += 1
            continue

        # Not installed — install it now (without --quiet so we can see errors)
        print(f"        Installing {pkg_name}...", end=" ", flush=True)
        try:
            # First attempt: regular install (no --upgrade, no --quiet)
            result = subprocess.run(
                [str(VENV_PYTHON), "-m", "pip", "install", pkg],
                capture_output=True, text=True, timeout=600
            )
            if result.returncode != 0:
                # Retry with --upgrade --force-reinstall as a fallback
                result = subprocess.run(
                    [str(VENV_PYTHON), "-m", "pip", "install", "--upgrade",
                     "--force-reinstall", "--no-deps", pkg],
                    capture_output=True, text=True, timeout=600
                )

            # Verify the package is now importable
            verify = subprocess.run(
                [str(VENV_PYTHON), "-c", f"import {import_name}"],
                capture_output=True, timeout=15
            )
            if verify.returncode == 0:
                print(_green("OK"))
                succeeded += 1
                installed_now.append(pkg_name)
            else:
                print(_red("FAILED (import check)"))
                err = result.stderr.strip()[-300:] if result.stderr else ""
                if err:
                    print(f"          {err[-200:]}")
                failed.append(pkg_name)
        except subprocess.TimeoutExpired:
            print(_red("TIMEOUT"))
            failed.append(pkg_name)
        except Exception as e:
            print(_red(f"ERROR ({e})"))
            failed.append(pkg_name)

    # Special: if browser-use was just installed, ensure playwright chromium is too
    if "browser-use" in installed_now:
        print(f"        Installing Playwright Chromium browser...", end=" ", flush=True)
        try:
            result = subprocess.run(
                [str(VENV_PYTHON), "-m", "playwright", "install", "chromium"],
                capture_output=True, text=True, timeout=600
            )
            if result.returncode == 0:
                print(_green("OK"))
            else:
                print(_yellow("already installed or skipped"))
        except Exception as e:
            print(_yellow(f"skipped ({e})"))

    if not failed:
        if installed_now:
            _ok(f"All {succeeded} dependencies OK ({len(installed_now)} newly installed)")
        else:
            _ok(f"All {succeeded} dependencies up to date")
    elif succeeded > 0:
        _warn(f"{succeeded} OK, {len(failed)} failed: {', '.join(failed)}")
    else:
        _fail("All dependencies failed to update")

    return True


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

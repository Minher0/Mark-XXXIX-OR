#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════╗
║  JARVIS — Mark XXXIX Launcher                       ║
║  Self-installing executable                          ║
║                                                      ║
║  On first run:                                       ║
║    1. Checks Python 3.10+                            ║
║    2. Clones the GitHub repo                         ║
║    3. Creates a virtual environment                  ║
║    4. Installs all pip dependencies                  ║
║    5. Creates a desktop shortcut                     ║
║    6. Launches Jarvis                                ║
║                                                      ║
║  On subsequent runs: just launches Jarvis instantly  ║
╚══════════════════════════════════════════════════════╝
"""

import os
import sys
import subprocess
import shutil
import json
import urllib.request
import urllib.error
import zipfile
import io
import time
from pathlib import Path

# ─── Constants ───────────────────────────────────────────────

APP_NAME     = "Jarvis"
GITHUB_REPO  = "https://github.com/Minher0/Mark-XXXIX-OR.git"
GITHUB_ZIP   = "https://github.com/Minher0/Mark-XXXIX-OR/archive/refs/heads/main.zip"
MIN_PYTHON   = (3, 10)
MAX_PYTHON   = (3, 13)  # Python 3.14+ is pre-release / no PyQt6 wheels yet

LOCAL_APP    = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
APP_DIR      = LOCAL_APP / APP_NAME
REPO_DIR     = APP_DIR / "app"
VENV_DIR     = APP_DIR / "venv"
CONFIG_DIR   = APP_DIR / "config"
REQUIREMENTS = REPO_DIR / "requirements.txt"

# Venv Python paths (Windows)
VENV_PYTHON  = VENV_DIR / "Scripts" / "python.exe"
VENV_PIP     = VENV_DIR / "Scripts" / "pip.exe"


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

def _warn(msg: str) -> None:
    print(_yellow(f"  ⚠ {msg}"))


# ─── Checks ──────────────────────────────────────────────────

def is_installed() -> bool:
    """Check if Jarvis is already set up."""
    return (REPO_DIR / "main.py").exists() and VENV_PYTHON.exists()


def _is_frozen() -> bool:
    """Check if running as a PyInstaller executable."""
    return getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS')


def _parse_python_version(version_str: str) -> tuple[int, int, int, bool]:
    """Parse a Python version string like 'Python 3.11.9' or 'Python 3.14.0a4'.
    Returns (major, minor, patch, is_prerelease)."""
    version_str = version_str.replace("Python", "").strip()
    is_prerelease = any(c in version_str for c in "abcrc")  # alpha/beta/rc
    # Remove prerelease suffix for parsing
    for suffix in "abcrc":
        if suffix in version_str:
            version_str = version_str.split(suffix)[0].rstrip(".")
            break
    parts = version_str.split(".")
    try:
        major = int(parts[0]) if len(parts) > 0 else 0
        minor = int(parts[1]) if len(parts) > 1 else 0
        patch = int(parts[2]) if len(parts) > 2 else 0
    except ValueError:
        return (0, 0, 0, True)
    return (major, minor, patch, is_prerelease)


def _find_system_python() -> str | None:
    """Find the real system Python (not the PyInstaller exe).
    Prefers stable releases (3.10-3.13) over pre-release versions (3.14+ alpha/beta)."""
    # When frozen, sys.executable points to the .exe, not Python.
    # We need to find the actual Python interpreter on the system.

    # Strategy: try versioned names FIRST (most specific), then generic
    # This ensures we pick a stable version if available
    candidates = []

    # Versioned names - stable versions first (3.13 down to 3.10)
    for minor in range(13, 9, -1):
        candidates.append(f"py -3.{minor}")
        candidates.append(f"python3.{minor}")

    # Common install paths on Windows (specific versions first)
    if os.name == "nt":
        local_app = os.environ.get("LOCALAPPDATA", "")
        program_files = os.environ.get("ProgramFiles", "C:\\Program Files")
        program_files_x86 = os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)")

        for base in [local_app, program_files, program_files_x86]:
            if not base:
                continue
            for minor in range(13, 9, -1):
                candidates.append(
                    os.path.join(base, f"Programs\\Python\\Python3{minor}\\python.exe")
                )

    # Generic names (lowest priority - may return any version)
    candidates.extend([
        "py",
        "python",
        "python3",
    ])

    # Track the best candidate (stable > prerelease)
    best_stable = None
    best_stable_version = (0, 0, 0)
    best_prerelease = None
    best_prerelease_version = (0, 0, 0)

    for candidate in candidates:
        try:
            # Handle "py -3.12" style candidates
            cmd = candidate.split() if " " in candidate else [candidate]
            result = subprocess.run(
                cmd + ["--version"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                version_str = result.stdout.strip() or result.stderr.strip()
                major, minor, patch, is_prerelease = _parse_python_version(version_str)
                v = (major, minor)

                if v < MIN_PYTHON:
                    continue  # Too old

                version_tuple = (major, minor, patch)

                if is_prerelease or v > MAX_PYTHON:
                    # Pre-release or too new — keep as fallback only
                    if version_tuple > best_prerelease_version:
                        best_prerelease = candidate
                        best_prerelease_version = version_tuple
                else:
                    # Stable and in range — prefer this
                    if version_tuple > best_stable_version:
                        best_stable = candidate
                        best_stable_version = version_tuple

        except Exception:
            continue

    # Prefer stable Python, fall back to pre-release if nothing else
    if best_stable:
        return best_stable
    if best_prerelease:
        _warn(f"Only Python pre-release found ({best_prerelease_version}). Some packages may not work.")
        return best_prerelease
    return None


def check_python() -> str | None:
    """Check that Python 3.10+ is available. Returns path or None."""
    # If running as PyInstaller .exe, NEVER use sys.executable
    if _is_frozen():
        return _find_system_python()

    # Try current Python first (only if NOT frozen)
    py_version = sys.version_info[:2]
    if py_version >= MIN_PYTHON:
        return sys.executable

    # Fallback to searching the system
    return _find_system_python()


def check_git() -> str | None:
    """Check if git is available. Returns path or None."""
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


# ─── Installation steps ──────────────────────────────────────

def install_git() -> bool:
    """Try to install git via winget."""
    print(_yellow("\n  Git is not installed. Attempting to install via winget..."))
    try:
        result = subprocess.run(
            ["winget", "install", "Git.Git", "--accept-source-agreements", "--accept-package-agreements"],
            capture_output=True, text=True, timeout=300
        )
        if result.returncode == 0:
            _ok("Git installed successfully")
            return True
    except Exception as e:
        _fail(f"Could not install git: {e}")
    _warn("Please install git manually: https://git-scm.com/download/win")
    return False


def setup_app_dir() -> bool:
    """Create the application directory structure."""
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        REPO_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except Exception as e:
        _fail(str(e))
        return False


def clone_repo() -> bool:
    """Clone the GitHub repository. Falls back to ZIP download."""
    # If main.py already exists, skip
    if (REPO_DIR / "main.py").exists():
        _ok("Already cloned")
        return True

    git = check_git()

    if git:
        print(f"      Cloning from {GITHUB_REPO}...")
        try:
            result = subprocess.run(
                ["git", "clone", GITHUB_REPO, str(REPO_DIR)],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode == 0:
                _ok("Repository cloned")
                return True
            _warn(f"git clone failed: {result.stderr.strip()[:100]}")
        except subprocess.TimeoutExpired:
            _warn("git clone timed out")
        except Exception as e:
            _warn(f"git clone error: {e}")

    # Fallback: download ZIP
    print("      Downloading from GitHub (ZIP)...")
    try:
        req = urllib.request.Request(GITHUB_ZIP, headers={
            "User-Agent": "Jarvis-Launcher/1.0"
        })
        with urllib.request.urlopen(req, timeout=60) as response:
            zip_data = response.read()

        with zipfile.ZipFile(io.BytesIO(zip_data)) as zf:
            # GitHub ZIPs have a root folder like "Mark-XXXIX-OR-main/"
            extract_dir = APP_DIR / "_temp_extract"
            zf.extractall(str(extract_dir))

            # Find the extracted folder
            extracted = list(extract_dir.iterdir())
            if extracted:
                src = extracted[0]
                # Move contents to REPO_DIR
                if REPO_DIR.exists():
                    shutil.rmtree(str(REPO_DIR))
                shutil.move(str(src), str(REPO_DIR))
                shutil.rmtree(str(extract_dir), ignore_errors=True)

        _ok("Downloaded and extracted")
        return True

    except Exception as e:
        _fail(f"Download failed: {e}")
        return False


def create_venv() -> bool:
    """Create a Python virtual environment."""
    if VENV_PYTHON.exists():
        _ok("Already exists")
        return True

    python_path = check_python()
    if not python_path:
        _fail("Python not found!")
        return False

    # Build the command list for running Python
    # "py -3.11" needs to be split, full paths can be used directly
    if " " in python_path and not os.path.isfile(python_path):
        # Likely a multi-part command like "py -3.11"
        py_cmd = python_path.split()
    else:
        py_cmd = [python_path]

    print(f"      Creating venv at {VENV_DIR}...")
    print(f"      Using Python: {python_path}")

    # Try creating venv with ensurepip first
    try:
        result = subprocess.run(
            py_cmd + ["-m", "venv", str(VENV_DIR)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and VENV_PYTHON.exists():
            _ok("Virtual environment created")
            return True

        # Show the actual error for debugging
        err_msg = result.stderr.strip() if result.stderr else ""
        out_msg = result.stdout.strip() if result.stdout else ""
        _warn(f"venv creation failed: {(err_msg or out_msg)[:200]}")
    except subprocess.TimeoutExpired:
        _warn("venv creation timed out")
    except Exception as e:
        _warn(f"venv error: {e}")

    # Fallback: try with --without-pip (pip will be bootstrapped later)
    _warn("Retrying without pip (will bootstrap pip separately)...")
    try:
        # Clean up partial venv first
        if VENV_DIR.exists():
            shutil.rmtree(str(VENV_DIR), ignore_errors=True)

        result = subprocess.run(
            py_cmd + ["-m", "venv", "--without-pip", str(VENV_DIR)],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode == 0 and VENV_PYTHON.exists():
            _ok("Virtual environment created (without pip)")
            # Bootstrap pip manually using get-pip.py
            _bootstrap_pip()
            return True

        err_msg = result.stderr.strip() if result.stderr else ""
        _fail(f"Cannot create venv: {(err_msg)[:200]}")
        return False
    except Exception as e:
        _fail(str(e))
        return False


def _bootstrap_pip() -> None:
    """Bootstrap pip in the virtual environment using get-pip.py."""
    print("      Bootstrapping pip...")
    try:
        # Download get-pip.py
        get_pip_url = "https://bootstrap.pypa.io/get-pip.py"
        get_pip_path = VENV_DIR / "get-pip.py"
        urllib.request.urlretrieve(get_pip_url, str(get_pip_path))

        # Run get-pip.py in the venv
        result = subprocess.run(
            [str(VENV_PYTHON), str(get_pip_path)],
            capture_output=True, text=True, timeout=60
        )
        if result.returncode == 0:
            _ok("pip bootstrapped successfully")
        else:
            _warn("pip bootstrap failed, will retry during dependency install")

        # Clean up
        if get_pip_path.exists():
            get_pip_path.unlink()
    except Exception as e:
        _warn(f"pip bootstrap error: {e}")


def install_deps() -> bool:
    """Install pip dependencies from requirements.txt.
    Installs packages one by one so one failure doesn't block others."""
    if not REQUIREMENTS.exists():
        _fail(f"requirements.txt not found at {REQUIREMENTS}")
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
        _warn("No packages found in requirements.txt")
        return False

    print(f"      Installing {len(packages)} packages...")

    failed = []
    succeeded = 0

    for pkg in packages:
        pkg_name = pkg.split("==")[0].split(">=")[0].split("<=")[0].split("[")[0].strip()
        print(f"        Installing {pkg_name}...", end=" ", flush=True)
        try:
            result = subprocess.run(
                [str(VENV_PYTHON), "-m", "pip", "install", pkg, "--quiet"],
                capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                print(_green("OK"))
                succeeded += 1
            else:
                err = result.stderr.strip()
                # Check if it's already installed (not really a failure)
                if "already satisfied" in (result.stdout or "").lower():
                    print(_green("OK (already installed)"))
                    succeeded += 1
                else:
                    print(_red("FAILED"))
                    if err:
                        print(f"          {err[:120]}")
                    failed.append(pkg_name)
        except subprocess.TimeoutExpired:
            print(_red("TIMEOUT"))
            failed.append(pkg_name)
        except Exception as e:
            print(_red("ERROR"))
            failed.append(pkg_name)

    if not failed:
        _ok(f"All {succeeded} dependencies installed")
    elif succeeded > 0:
        _warn(f"{succeeded} installed, {len(failed)} failed: {', '.join(failed)}")
    else:
        _fail("All dependencies failed to install")

    return True  # Continue anyway — some deps are optional


def create_shortcut() -> bool:
    """Create a desktop shortcut and a start menu entry for Jarvis."""
    success = False

    # --- 1. Create Jarvis.bat on Desktop (most reliable) ---
    try:
        desktop = Path.home() / "Desktop"
        bat_path = desktop / "Jarvis.bat"
        bat_content = f'''@echo off
title JARVIS - AI Assistant
cd /d "{REPO_DIR}"
"{VENV_PYTHON}" "{REPO_DIR / "main.py"}"
if errorlevel 1 (
    echo.
    echo Jarvis exited with an error.
    pause
)
'''
        bat_path.write_text(bat_content, encoding="utf-8")
        _ok("Desktop shortcut (Jarvis.bat) created")
        success = True
    except Exception as e:
        _warn(f"Could not create Desktop bat: {e}")

    # --- 2. Create .lnk shortcut on Desktop ---
    try:
        desktop = Path.home() / "Desktop"
        shortcut_path = desktop / "Jarvis.lnk"

        # Use PowerShell to create a shortcut
        ps_script = f'''
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
$Shortcut.TargetPath = "{VENV_PYTHON}"
$Shortcut.Arguments = '"{REPO_DIR / "main.py"}"'
$Shortcut.WorkingDirectory = "{REPO_DIR}"
$Shortcut.IconLocation = "{REPO_DIR / "Jarvis-logo.ico"},0"
$Shortcut.Description = "JARVIS - AI Assistant"
$Shortcut.Save()
'''
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script],
            capture_output=True, timeout=10
        )
        if shortcut_path.exists():
            if not success:
                _ok("Desktop shortcut created")
            success = True
    except Exception:
        pass

    # --- 3. Create Start Menu shortcut ---
    try:
        start_menu = Path.home() / "AppData" / "Roaming" / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        start_menu.mkdir(parents=True, exist_ok=True)
        sm_shortcut = start_menu / "Jarvis.lnk"

        ps_script_sm = f'''
$WshShell = New-Object -ComObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{sm_shortcut}")
$Shortcut.TargetPath = "{VENV_PYTHON}"
$Shortcut.Arguments = '"{REPO_DIR / "main.py"}"'
$Shortcut.WorkingDirectory = "{REPO_DIR}"
$Shortcut.IconLocation = "{REPO_DIR / "Jarvis-logo.ico"},0"
$Shortcut.Description = "JARVIS - AI Assistant"
$Shortcut.Save()
'''
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_script_sm],
            capture_output=True, timeout=10
        )
    except Exception:
        pass

    if not success:
        _warn("Could not create desktop shortcut (non-critical)")
    return True


def create_config_if_needed() -> bool:
    """Create default api_keys.json if it doesn't exist."""
    config_file = REPO_DIR / "config" / "api_keys.json"
    if config_file.exists():
        return True

    example = REPO_DIR / "config" / "api_keys.example.json"
    try:
        if example.exists():
            shutil.copy(str(example), str(config_file))
        else:
            config_file.write_text(json.dumps({
                "gemini_api_key": "",
                "discord_token": ""
            }, indent=2), encoding="utf-8")
    except Exception:
        pass

    return True


# ─── Launch ──────────────────────────────────────────────────

def launch_jarvis() -> None:
    """Launch Jarvis using the venv Python."""
    if not VENV_PYTHON.exists():
        print(_red("  Virtual environment not found!"))
        print(_yellow("  Please run Jarvis.exe to reinstall."))
        input("\n  Press Enter to exit...")
        return

    if not (REPO_DIR / "main.py").exists():
        print(_red("  main.py not found!"))
        print(_yellow("  Please run Jarvis.exe to reinstall."))
        input("\n  Press Enter to exit...")
        return

    print()
    print(_bold("  ╔══════════════════════════════════════╗"))
    print(_bold("  ║       JARVIS IS NOW LAUNCHING        ║"))
    print(_bold("  ╚══════════════════════════════════════╝"))
    print()

    try:
        os.chdir(str(REPO_DIR))
        # Use the same console window
        result = subprocess.run([str(VENV_PYTHON), str(REPO_DIR / "main.py")])
        # If Jarvis exits with an error code, keep window open so user can see
        if result.returncode != 0:
            print(_red(f"\n  Jarvis exited with error code {result.returncode}"))
            input("  Press Enter to exit...")
    except KeyboardInterrupt:
        print("\n  Jarvis shutting down...")
    except Exception as e:
        print(_red(f"\n  Error launching Jarvis: {e}"))
        input("\n  Press Enter to exit...")


# ─── Main ────────────────────────────────────────────────────

def main():
    print()
    print(_bold("  ╔════════════════════════════════════════════════╗"))
    print(_bold("  ║   JARVIS — Mark XXXIX OR   ·   Launcher       ║"))
    print(_bold("  ╚════════════════════════════════════════════════╝"))
    print()

    # ── Fast path: already installed ──
    if is_installed():
        print(_green("  ✓ Jarvis is installed. Launching..."))
        launch_jarvis()
        return

    # ── Slow path: first-time setup ──
    print(_bold("  First-time setup detected. Installing Jarvis...\n"))

    TOTAL_STEPS = 7

    # Step 1: Check Python
    _step(1, TOTAL_STEPS, "Checking Python...")
    python_path = check_python()
    if not python_path:
        _fail("Python 3.10+ not found!")
        print(_yellow("\n  Please install Python 3.10 or later:"))
        print(_cyan("    https://www.python.org/downloads/"))
        print(_yellow("  Make sure to check 'Add Python to PATH' during installation."))
        input("\n  Press Enter after installing Python to retry...")
        python_path = check_python()
        if not python_path:
            print(_red("  Python still not found. Cannot continue."))
            input("  Press Enter to exit...")
            sys.exit(1)
    # Get the actual Python version from the found python_path
    try:
        py_cmd = python_path.split() if " " in python_path else [python_path]
        ver_result = subprocess.run(
            py_cmd + ["--version"],
            capture_output=True, text=True, timeout=5
        )
        ver_str = ver_result.stdout.strip() if ver_result.returncode == 0 else f"Python ({python_path})"
    except Exception:
        ver_str = f"Python ({python_path})"
    _ok(ver_str)

    # Warn if using a pre-release Python version
    _, _, _, is_prerelease = _parse_python_version(ver_str)
    if is_prerelease:
        _warn("Python pre-release detected! Some packages (PyQt6, etc.) may not have")
        _warn("compatible wheels and could crash. Consider installing Python 3.10-3.13.")

    # Step 2: Check/Install Git
    _step(2, TOTAL_STEPS, "Checking Git...")
    git = check_git()
    if git:
        _ok("Git available")
    else:
        _warn("Git not found")
        if not install_git():
            print(_yellow("  Will use ZIP download instead (no auto-updates)."))
        else:
            _ok("Git installed")

    # Step 3: Setup directories
    _step(3, TOTAL_STEPS, "Creating directories...")
    if not setup_app_dir():
        print(_red("  Cannot create application directory. Check permissions."))
        input("  Press Enter to exit...")
        sys.exit(1)
    _ok()

    # Step 4: Clone repo
    _step(4, TOTAL_STEPS, "Downloading Jarvis...")
    if not clone_repo():
        print(_red("  Cannot download Jarvis. Check your internet connection."))
        input("  Press Enter to exit...")
        sys.exit(1)

    # Step 5: Create venv
    _step(5, TOTAL_STEPS, "Creating virtual environment...")
    if not create_venv():
        print(_red("  Cannot create virtual environment."))
        input("  Press Enter to exit...")
        sys.exit(1)

    # Step 6: Install dependencies
    _step(6, TOTAL_STEPS, "Installing dependencies (this may take a minute)...")
    install_deps()

    # Step 7: Create shortcut & config
    _step(7, TOTAL_STEPS, "Finishing setup...")
    create_config_if_needed()
    create_shortcut()
    _ok()

    print()
    print(_green(_bold("  ╔══════════════════════════════════════╗")))
    print(_green(_bold("  ║     JARVIS INSTALLED SUCCESSFULLY    ║")))
    print(_green(_bold("  ╚══════════════════════════════════════╝")))
    print()
    print(f"  App location:  {_cyan(str(REPO_DIR))}")
    print(f"  Config:        {_cyan(str(REPO_DIR / 'config' / 'api_keys.json'))}")
    print(f"  Venv:          {_cyan(str(VENV_DIR))}")
    print()
    print(_yellow("  Tip: Run Updater.exe to check for updates."))
    print(_yellow("  Tip: You can edit files directly in the app folder."))
    print()

    time.sleep(2)
    launch_jarvis()


if __name__ == "__main__":
    main()

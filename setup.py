import subprocess
import sys
import platform

print("=" * 60)
print("  JARVIS — Setup")
print("=" * 60)

# ── 1. Install Python dependencies ──
print("\n[1/4] 📦 Installing Python packages...")
subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], check=True)

# ── 2. Install Playwright browsers ──
print("\n[2/4] 🌐 Installing Playwright browsers...")
subprocess.run([sys.executable, "-m", "playwright", "install"], check=True)

# ── 3. Install Ollama (for local mode) ──
print("\n[3/4] 🤖 Setting up Ollama for local mode...")
system = platform.system()

ollama_installed = False
try:
    result = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=5)
    if result.returncode == 0:
        print(f"  ✅ Ollama already installed: {result.stdout.strip()}")
        ollama_installed = True
except (FileNotFoundError, subprocess.TimeoutExpired):
    pass

if not ollama_installed:
    print("  📥 Installing Ollama...")
    if system == "Windows":
        try:
            subprocess.run(
                ["winget", "install", "Ollama.Ollama", "--accept-source-agreements", "--accept-package-agreements"],
                capture_output=True, text=True, timeout=300
            )
            print("  ✅ Ollama installed via winget")
            ollama_installed = True
        except Exception as e:
            print(f"  ⚠️ winget install failed: {e}")
            print("  Please install manually: https://ollama.com/download")
    elif system == "Darwin":
        try:
            subprocess.run(["brew", "install", "ollama"], capture_output=True, text=True, timeout=300)
            print("  ✅ Ollama installed via Homebrew")
            ollama_installed = True
        except Exception:
            print("  Please install manually: https://ollama.com/download")
    else:
        try:
            subprocess.run(
                ["bash", "-c", "curl -fsSL https://ollama.com/install.sh | sh"],
                capture_output=True, text=True, timeout=300
            )
            print("  ✅ Ollama installed via install script")
            ollama_installed = True
        except Exception:
            print("  Please install manually: https://ollama.com/download")

# ── 4. Pull default model ──
print("\n[4/4] 📥 Downloading default AI model (qwen2.5:7b)...")
if ollama_installed:
    try:
        # Start Ollama service if not running
        if system == "Windows":
            subprocess.Popen(
                ["ollama", "serve"],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["ollama", "serve"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        import time
        time.sleep(3)

        # Pull the model
        print("  Downloading qwen2.5:7b (~4.7 GB, first time only)...")
        subprocess.run(["ollama", "pull", "qwen2.5:7b"], timeout=600)
        print("  ✅ qwen2.5:7b ready")
    except Exception as e:
        print(f"  ⚠️ Could not pull model: {e}")
        print("  You can pull it later with: ollama pull qwen2.5:7b")
else:
    print("  ⏭️ Skipped (Ollama not installed)")

# ── Done ──
print("\n" + "=" * 60)
print("  ✅ Setup complete!")
print()
print("  Usage:")
print("    python main.py          → Cloud mode (Gemini API)")
print("    python main.py --local  → Local mode (Ollama, no API key needed)")
print()
print("  Local mode models (run 'ollama pull <model>'):")
print("    qwen2.5:7b    → Fast, good for most tasks (default)")
print("    qwen2.5:14b   → Smarter, needs more RAM (~8GB)")
print("    llama3.1:8b   → Alternative model")
print("=" * 60)

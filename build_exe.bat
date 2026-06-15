@echo off
chcp 65001 >nul 2>&1
title JARVIS — Build Executables

echo.
echo   ╔════════════════════════════════════════════════╗
echo   ║   JARVIS — Build Executables with PyInstaller  ║
echo   ╚════════════════════════════════════════════════╝
echo.

:: ── Step 1: Check Python ──
echo   [1/4] Checking Python...
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo   ✗ Python not found! Install Python 3.10+ first.
    pause
    exit /b 1
)
echo   ✓ Python found

:: ── Step 2: Install PyInstaller ──
echo   [2/4] Installing PyInstaller...
pip install pyinstaller --quiet --upgrade
if %errorlevel% neq 0 (
    echo   ✗ Failed to install PyInstaller
    pause
    exit /b 1
)
echo   ✓ PyInstaller ready

:: ── Step 3: Clean previous builds ──
echo   [3/4] Cleaning previous builds...
if exist "build" rmdir /s /q build
if exist "dist"  rmdir /s /q dist
echo   ✓ Clean

:: ── Step 4: Build executables ──
echo   [4/4] Building executables...
echo.

:: Build Jarvis.exe (launcher)
echo   Building Jarvis.exe...
pyinstaller --onefile --console --name "Jarvis" --icon="Jarvis-logo.ico" --distpath="dist" launcher.py
if %errorlevel% neq 0 (
    echo   ✗ Failed to build Jarvis.exe
    pause
    exit /b 1
)
echo   ✓ Jarvis.exe built

echo.

:: Build Updater.exe
echo   Building Updater.exe...
pyinstaller --onefile --console --name "Updater" --icon="Jarvis-logo.ico" --distpath="dist" updater.py
if %errorlevel% neq 0 (
    echo   ✗ Failed to build Updater.exe
    pause
    exit /b 1
)
echo   ✓ Updater.exe built

echo.
echo   ╔════════════════════════════════════════════════╗
echo   ║          BUILD SUCCESSFUL!                     ║
echo   ╚════════════════════════════════════════════════╝
echo.
echo   Output files in dist\:
echo     • Jarvis.exe   — Self-installing launcher
echo     • Updater.exe  — Auto-updater
echo.
echo   Share these two .exe files together!
echo   Users just double-click Jarvis.exe to install and run.
echo.

:: Open the dist folder
explorer dist

pause

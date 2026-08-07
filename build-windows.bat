@echo off
REM ============================================================
REM  Lantern — Windows build (Tauri 2 + Python sidecar)
REM  One-time prerequisites:
REM    1. Rust:        winget install Rustlang.Rustup
REM    2. Tauri CLI:   cargo install tauri-cli --version "^2"
REM    3. Python deps: pip install -r requirements.txt pyinstaller
REM ============================================================

echo [1/3] Building the Python sidecar...
pyinstaller --noconfirm --onefile --name lantern-server ^
  --add-data "ui\index.html;ui" ^
  sidecar.py
if errorlevel 1 exit /b 1

echo [2/3] Placing sidecar where Tauri expects it (target-triple name)...
if not exist src-tauri\binaries mkdir src-tauri\binaries
copy /Y dist\lantern-server.exe src-tauri\binaries\lantern-server-x86_64-pc-windows-msvc.exe

echo [3/3] Building the Tauri app + installer...
cd src-tauri
cargo tauri build
cd ..

echo.
echo Done. Installers are in src-tauri\target\release\bundle\
echo   msi\Lantern_1.0.0_x64_en-US.msi
echo   nsis\Lantern_1.0.0_x64-setup.exe
echo.
echo mcp.json lives at %%APPDATA%%\Lantern\mcp.json (created on first run).

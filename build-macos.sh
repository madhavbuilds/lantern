#!/usr/bin/env bash
# Lantern — macOS desktop build (Tauri 2 + Python sidecar)
set -euo pipefail
cd "$(dirname "$0")"

TRIPLE="$(rustc -vV | awk '/^host:/{print $2}')"
echo "[1/3] Building UI…"
npm run build:ui

echo "[2/3] Building Python sidecar…"
python3 -m pip install -q -r requirements.txt pyinstaller
python3 -m PyInstaller --noconfirm --onefile --name lantern-server \
  --add-data "ui:ui" \
  sidecar.py

mkdir -p src-tauri/binaries
cp -f "dist/lantern-server" "src-tauri/binaries/lantern-server-${TRIPLE}"
chmod +x "src-tauri/binaries/lantern-server-${TRIPLE}"

echo "[3/3] Building Tauri .app / .dmg…"
npm exec -- tauri build

echo
echo "Done. App bundle is under src-tauri/target/release/bundle/"

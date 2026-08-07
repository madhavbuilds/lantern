"""
Sidecar entry point for the Tauri shell. Runs only the FastAPI server —
the window itself belongs to Tauri.

Build (Windows):
  pyinstaller --onefile --name lantern-server sidecar.py
then rename with the target triple Tauri expects:
  lantern-server-x86_64-pc-windows-msvc.exe -> src-tauri/binaries/
"""

import argparse

import uvicorn


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=7451)
    args = p.parse_args()
    uvicorn.run("server:app", host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()

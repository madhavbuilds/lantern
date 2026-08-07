@echo off
REM First-run setup for Lantern (Windows). Keep in sync with setup.sh.
cd /d "%~dp0"

if not exist mcp.json (
  echo {"servers":{}}> mcp.json
  echo Created mcp.json ^(empty^).
)

if not exist searxng\settings.yml (
  if not exist searxng\settings.example.yml (
    echo Missing searxng\settings.example.yml
    exit /b 1
  )
  copy /Y searxng\settings.example.yml searxng\settings.yml >nul
  python -c "import secrets,pathlib; p=pathlib.Path('searxng/settings.yml'); p.write_text(p.read_text().replace('CHANGE_ME_openssl_rand_hex_32', secrets.token_hex(32)))"
  echo Created searxng\settings.yml with a new secret_key.
)

echo.
echo Next ^(manual^):
echo   1. docker compose up -d
echo   2. ollama pull qwen3:4b ^&^& ollama create lantern-qwen -f Modelfile
echo   3. pip install -r requirements.txt
echo   4. uvicorn server:app --host 127.0.0.1 --port 3000
echo   5. Open http://127.0.0.1:3000

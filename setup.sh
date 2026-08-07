#!/usr/bin/env sh
# First-run setup for Lantern. POSIX sh. Under 30 lines.
set -eu
cd "$(dirname "$0")"

if [ ! -f mcp.json ]; then
  printf '%s\n' '{"servers":{}}' > mcp.json
  echo "Created mcp.json (empty — optional tools later)."
fi

if [ ! -f searxng/settings.yml ]; then
  if [ ! -f searxng/settings.example.yml ]; then
    echo "Missing searxng/settings.example.yml" >&2
    exit 1
  fi
  cp searxng/settings.example.yml searxng/settings.yml
  KEY=$(openssl rand -hex 32 2>/dev/null || python3 -c 'import secrets; print(secrets.token_hex(32))')
  # Portable-ish replace of the placeholder secret.
  KEY="$KEY" python3 -c '
import os, pathlib
p = pathlib.Path("searxng/settings.yml")
t = p.read_text()
p.write_text(t.replace("CHANGE_ME_openssl_rand_hex_32", os.environ["KEY"]))
'
  echo "Created searxng/settings.yml with a new secret_key."
fi

echo
echo "Next (manual):"
echo "  1. docker compose up -d"
echo "  2. ollama pull qwen3:4b && ollama create lantern-qwen -f Modelfile"
echo "  3. pip install -r requirements.txt"
echo "  4. uvicorn server:app --host 127.0.0.1 --port 3000"
echo "  5. Open http://127.0.0.1:3000"

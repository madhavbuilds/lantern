# Contributing

Thanks for helping. Keep changes small and explain *why*.

## Dev setup

```bash
./setup.sh          # or setup.bat on Windows
pip install -r requirements-dev.txt
docker compose up -d
ollama create lantern-qwen -f Modelfile
uvicorn server:app --host 127.0.0.1 --port 3000
```

Desktop shell (optional): `npm install` then `npm start` (needs Rust + the Tauri CLI).

## Tests and lint

```bash
ruff check .
ruff format --check .
python -m pytest tests/ -q
```

## Code style

- Python 3.11+. Public functions have type hints on parameters and returns.
- One way to do a thing — no parallel helpers for the same job.
- No new runtime dependencies without a clear reason in the PR.
- UI stays a static `ui/` folder (no bundler required for the web UI).

## Good PRs

- One concern per PR.
- Tests for behaviour you change (especially search follow-ups, cancel/stop, isolation).
- Update README / SECURITY if behaviour or trust boundaries change.

## Do not commit

- `mcp.json`, `*.db`, `searxng/settings.yml`, `.env`, `traces.jsonl`, `SPECS.md`
- `node_modules/`, `src-tauri/target/`, `src-tauri/binaries/lantern-server-*`
- Absolute paths from your machine

Copy from the `*.example.*` files instead.

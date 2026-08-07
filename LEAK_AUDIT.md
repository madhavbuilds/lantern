# Leak audit

**Date:** 2026-08-07  
**Scope:** Working tree at `/Users/maddy/Downloads/lantern`  
**Git status:** **There is no `.git` directory.** `git status` / `git ls-files` fail with
`fatal: not a git repository`. Nothing is tracked yet. This audit is a
**pre-init** inventory of what would leak on a naïve `git init && git add .`
with today's incomplete `.gitignore`.

---

## Verdict (read this first)

| Severity | Finding |
|----------|---------|
| **SECRET — ROTATE** | `searxng/settings.yml` contains a real `secret_key` hex value. Even though limiter is off locally, treat it as compromised if this tree was ever shared, pushed, or mirrored. **Generate a new key** (`openssl rand -hex 32`) before any public push. Removing the file from a future commit is not enough if it was already copied. |
| **MACHINE FINGERPRINT** | `SPECS.md` records a real host: Darwin 25.2.0, Apple M2, 8.6 GB RAM, Ollama 0.31.1, measured timings. Do not publish as-is; keep a sanitized example or leave it local. |
| **RUNTIME CONFIG** | `mcp.json` is present (currently empty `servers: {}`). Still must never be committed — real installs hold absolute paths / commands. |
| **USER DATA (outside tree)** | `~/.config/Lantern/chats.db` exists on this machine. Not in the repo tree. If a `chats.db` ever appears in the project root or is force-added, it is **real conversation history** and needs history purge + assume exposed. |
| **BUILD ARTEFACTS** | `src-tauri/binaries/lantern-server-*`, `src-tauri/target/`, `node_modules/`, `frontend/node_modules/`, `__pycache__/`, `.DS_Store`, `.pytest_cache/` — must stay untracked. |
| **ABSOLUTE PATHS** | Developer `/Users/maddy/...` paths appear under `src-tauri/target/` (build output). Placeholder `C:\Users\YOU\...` in `mcp.example.json` is fine. |

**No history rewrite was attempted** (no git repo; and even with one, that remains your call).

---

## Checklist vs working tree

| Pattern | Present? | Action |
|---------|----------|--------|
| `chats.db` / `*.db` in tree | No (DB lives at `~/.config/Lantern/chats.db`) | gitignore `*.db`; never add |
| `mcp.json` | **Yes** (empty servers) | gitignore; keep only `mcp.example.json` |
| `traces.jsonl` | No | gitignore anyway |
| `.env` | No | gitignore anyway |
| `searxng/settings.yml` | **Yes — has secret_key** | gitignore; ship `settings.example.yml`; **rotate key** |
| `SPECS.md` (real machine) | **Yes** | gitignore or replace with `SPECS.example.md` |
| Absolute `/Users/<name>/` in source | Not in `.py`/docs intended for ship | Build dirs contain `/Users/maddy/` — gitignore `target/` |
| `__pycache__`, `.venv`, `node_modules` | **Yes** | gitignore |
| `src-tauri/target/` | **Yes** | gitignore |
| `src-tauri/binaries/*` (except `.gitkeep`) | **Yes** (sidecar binaries) | gitignore binaries; keep `.gitkeep` |
| `*.spec` (PyInstaller) | No | gitignore anyway |
| `.DS_Store`, `.idea`, `.vscode` | `.DS_Store` **yes** | gitignore |
| Compiled sidecar in binaries/ | **Yes** | gitignore |

---

## If / when you `git init`

1. Apply the new `.gitignore` **before** the first `git add`.
2. Do **not** `git add -f` on `mcp.json`, `searxng/settings.yml`, `SPECS.md`, `*.db`, or binaries.
3. If any of those ever appear in `git ls-files` later:
   - `git rm --cached <file>` (keeps local copy)
   - For `chats.db` or a real secret: **purge history** with `git-filter-repo` or BFG — do not rely on a delete commit alone.
   - For `secret_key`: **rotate**, do not only delete.

---

## What this audit does *not* claim

- It does not scan past GitHub remotes or other clones (none configured here).
- It does not prove `secret_key` was never pasted into a chat or gist.
- Empty `mcp.json` today ≠ safe forever; the ignore rule is the protection.

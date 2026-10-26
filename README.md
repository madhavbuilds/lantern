# Lantern

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Lantern is a **local answer engine**: it searches the web with SearXNG, answers
with a model you run in Ollama, and keeps chat history on your machine.

<!-- TODO: add a real screenshot at docs/screenshot.png and uncomment below -->
<!-- ![Screenshot](docs/screenshot.png) -->

**Cost:** nothing. No API keys, no accounts, no subscription. Search and the
model both run on hardware you control.

### Highlights

- **Cited answers, streamed.** Numbered source cards and inline citation pills;
  hover a citation to preview the page it came from.
- **Watch it work.** A live pipeline (Route → Search → Read → Think → Write)
  with an elapsed timer, the model's reasoning streamed as it happens, and
  tokens/sec once it's done.
- **Thinking budget.** Reasoning models can spend 30-40s thinking before the
  first word on a laptop. Lantern caps the thought per intent and makes the
  model answer ("budget forcing"). On an 8 GB M2 the first word came about
  2-3× sooner.
- **Instant routing.** Greetings, arithmetic, writing tasks and web questions
  are told apart with rules, not an extra model call, so no time is spent
  before the answer starts.
- **Focus modes.** Web, News, Academic, Code or Reddit, plus Quick and Deep
  depth, right in the composer.
- **Search everything you've asked.** ⌘K full-text search over every question
  and answer, using a SQLite FTS5 index with BM25 ranking and stemming.
- **Projects.** Group chats under shared instructions. Each chat's history
  still stays private from the others.
- **Self-diagnosing.** Status lights for the model and search, and a first-run
  card with the exact command to fix whatever's missing.
- **Export** a thread as Markdown with its references, or all data as JSON.

---

## Requirements

| Need | Notes |
|------|--------|
| OS | macOS, Windows, or Linux |
| [Docker](https://docs.docker.com/get-docker/) | For SearXNG |
| [Ollama](https://ollama.com) | For the answer model |
| Python 3.11+ | For the server |
| RAM / VRAM | See table below — **CPU-only is usable but slow** |

Pick a model that fits **fully on the GPU**. Partial CPU/GPU offload is often
5–10× slower than a smaller model that stays on GPU.

| Hardware | Suggested base model |
|----------|----------------------|
| ~8 GB unified (Apple) / 6 GB NVIDIA | `qwen3:4b` |
| ~16 GB unified / 8 GB NVIDIA | `qwen3:8b` |
| 24 GB+ | `qwen3:14b` or larger |
| CPU only | `qwen3:4b`, Fast mode — expect long waits |

Then create Lantern’s wrapper model (sets context size Ollama’s OpenAI API
cannot set per request):

```bash
ollama pull qwen3:4b
ollama create lantern-qwen -f Modelfile
```

---

## Quickstart

From a clone of this repo:

```bash
./setup.sh
# Windows: setup.bat
```

That copies example configs and generates a SearXNG `secret_key`. Then:

```bash
docker compose up -d
pip install -r requirements.txt
ollama create lantern-qwen -f Modelfile   # if you have not already
uvicorn server:app --host 127.0.0.1 --port 3000
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000).

**Desktop window (optional):** needs Rust and Node.

```bash
npm install
npm start
```

That runs the Tauri shell, which talks to the Python sidecar on `127.0.0.1:7451`.
Docker and Ollama must still be running.

### Ollama keep-alive (recommended)

Without this, the first query after idle reloads the model (often several
seconds):

```bash
# macOS
launchctl setenv OLLAMA_KEEP_ALIVE -1
# then quit and reopen Ollama
```

```powershell
# Windows PowerShell — then restart Ollama from the tray
[Environment]::SetEnvironmentVariable("OLLAMA_KEEP_ALIVE", "-1", "User")
```

After one answer, check placement:

```bash
ollama ps
```

You want `100% GPU` (or equivalent). A CPU split means drop a model size.

---

## Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| Empty search / no sources | SearXNG JSON format off | Ensure `searxng/settings.yml` has `formats: [html, json]` (see the example file) |
| Everything feels stuck | Model spilling to CPU | `ollama ps` — if not 100% GPU, use a smaller model |
| First query very slow, later OK | Model unloaded | Set `OLLAMA_KEEP_ALIVE=-1` and restart Ollama |
| “model isn’t installed” | Create step skipped | `ollama create lantern-qwen -f Modelfile` |
| “Can’t reach the model” | Ollama not running | Start Ollama, then Retry |
| “Search isn’t responding” | SearXNG down | `docker compose up -d` |
| “Can’t reach Lantern’s server” | uvicorn / sidecar down | Start the server again |

### Tuning

| Variable | Default | What it does |
|----------|---------|--------------|
| `MODEL` | `lantern-qwen` | Ollama model name |
| `LLM_BASE` | `http://localhost:11434/v1` | Any OpenAI-compatible endpoint |
| `SEARXNG_URL` | `http://localhost:8080` | Falls back to DuckDuckGo when unreachable |
| `LANTERN_THINK_BUDGET` | `1` | `0` lets the model think as long as it likes |
| `LANTERN_PLANNER` | `heuristic` | `llm` uses a model call to route (set `PLANNER_MODEL` to a small non-thinking model) |
| `LANTERN_NO_THINK` | `0` | `1` appends `/no_think` — only for hybrid models like original `qwen3:8b` |
| `CHATS_DB` | `~/.config/Lantern/chats.db` | Where history lives |

Local measurements (optional): run `python bench.py` and keep results in a
private `SPECS.md` (gitignored). Do not commit machine fingerprints.

---

## How it works

```
Browser / Tauri UI  →  FastAPI (server.py)  →  Ollama (chat)
                         │
                         ├─ planner.py      instant intent/format routing
                         ├─ SearXNG         search
                         ├─ trafilatura     page text (Deep)
                         ├─ rank-bm25       pick context under BUDGET
                         └─ chats.py        SQLite history + FTS5 index
```

**Design choices**

- **BUDGET** (`fast` / `deep` token-ish word caps in `server.py`) keeps
  context small so local prefill stays tolerable. Raising it without GPU
  headroom makes first token slower, not smarter.
- **Budget forcing, not a smaller brain.** Past a per-intent thinking cap the
  server closes the `<think>` block itself and prefills the answer turn;
  Ollama's prefix cache keeps that second prefill cheap.
- **BM25, not embeddings** — ranking snippets/pages for a single query does
  not need a second model or a vector DB; BM25 is local, fast, and enough.
- **Python sidecar + Tauri shell** — search, budgets, and MCP stay in Python;
  Tauri provides a small native window and installer without a Rust rewrite
  of the pipeline.

Chats are isolated from each other. Follow-ups only see short in-chat
history. Optional “About you” and project instructions are user-written only.

See [SECURITY.md](SECURITY.md) for trust boundaries.

---

## What it deliberately doesn’t do

- Cloud LLM APIs or hosted “memory”
- Cross-chat automatic recall
- Encrypting `chats.db` at rest
- Replacing your browser or a general agent framework
- Guaranteeing citation completeness (uncited claims are observable, not blocked)

---

## License and credits

Lantern is [MIT](LICENSE). Fill in the copyright name/year in `LICENSE` before
you publish a release.

### NOTICE — third-party

| Component | Role | License |
|-----------|------|---------|
| [SearXNG](https://github.com/searxng/searxng) | Search (Docker, separate process) | **AGPL-3.0** — you run their image; if you modify and redistribute SearXNG itself, AGPL applies to that work |
| [Ollama](https://ollama.com) | Local model runner | MIT |
| [trafilatura](https://github.com/adbar/trafilatura) | HTML → text | Apache-2.0 |
| [rank-bm25](https://github.com/dorianbrown/rank_bm25) | Lexical ranking | Apache-2.0 |
| [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) | Tool servers | MIT |
| [FastAPI](https://fastapi.tiangolo.com/) / [Uvicorn](https://www.uvicorn.org/) / [httpx](https://www.python-httpx.org/) | HTTP stack | MIT / BSD-3 / BSD-3 |
| [Tauri](https://tauri.app/) | Desktop shell | MIT / Apache-2.0 |
| Instrument Serif, Geist, Geist Mono | UI fonts (Google Fonts) | OFL-1.1 |

No AGPL code is linked into the Lantern Python package; SearXNG is an
external service. Still credit it, and do not strip its license from any
SearXNG forks you distribute.

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
and [SECURITY.md](SECURITY.md).

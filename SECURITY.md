# Security

Lantern is a **local** answer engine. Treat it that way.

## Reporting a vulnerability

Email **TODO: SECURITY CONTACT EMAIL** with a description and steps to
reproduce. Please do not open a public issue for exploitable bugs until a fix
is available.

## Network

- The API binds **`127.0.0.1` only** (see `sidecar.py` / uvicorn flags). Do not
  expose port `7451` or `3000` to a LAN or the internet.
- CORS is permissive (`allow_origins=["*"]`) **only because** the server is
  loopback-bound. Changing the bind address without locking CORS down would be
  unsafe.
- Deep mode fetches public web pages. Hosts resolve to public addresses only;
  private, loopback, and link-local targets are rejected (including each
  redirect hop). This reduces SSRF risk; it does not stop every DNS-rebinding
  race.

## Untrusted content

- Search results, page titles, URLs, and fetched HTML are **untrusted**. The UI
  escapes HTML and allows only `http:`/`https:` link hrefs.
- Fetched text is wrapped in delimiters; the system prompt says those spans are
  DATA, never instructions. **This mitigates prompt injection; it does not
  eliminate it.** With connected apps (MCP) enabled, a hostile page can still
  try to steer tool use.

## Connected apps (MCP)

- `mcp.json` lists commands you chose. Lantern starts them as **subprocesses**
  (no shell). A misconfigured or malicious server can read local files or reach
  the network as your user. Only add servers you trust.

## Chat data

- History lives in a local **SQLite** file (`chats.db`). Nothing is transmitted
  for storage.
- **Chats are isolated** from each other.
- **Not encrypted at rest.** Anyone with access to the machine login can read
  `chats.db` or a JSON export.
- Paths: `CHATS_DB` env → `%APPDATA%/Lantern/chats.db` (Windows) or
  `~/.config/Lantern/chats.db` → `./chats.db` beside the app.
- Delete via Settings → Your data, or delete the file. Uninstalling the app
  does **not** remove the database.

## Secrets

- Do not commit API keys, `mcp.json`, or `searxng/settings.yml`.
- Rotate the SearXNG `secret_key` if a real config was ever shared or pushed.

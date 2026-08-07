"""
MCP integration for Lantern.

Reads mcp.json, launches each configured server as a stdio subprocess, and
exposes their tools in OpenAI function-calling format so the local model can
use them. Everything here is free: the MCP SDK is MIT, the servers run on
your machine, and no key is involved anywhere.

mcp.json format (same shape Claude Desktop uses, so configs are portable):

  {
    "servers": {
      "filesystem": {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "C:\\Users\\you\\Documents"]
      },
      "playwright": {
        "command": "npx",
        "args": ["-y", "@playwright/mcp@latest"],
        "init_timeout": 180,
        "call_timeout": 45,
        "deep_only": true
      }
    }
  }

Design choices, all in service of latency:
  - Servers start once at app launch and stay up. Spawning npx per query
    would cost seconds.
  - Startup is concurrent so one slow server (Playwright downloading
    Chromium) can't serialize the rest — and runs in the background so the
    app can serve requests while init finishes.
  - Tool schemas are cached; listing happens once per server.
  - Tool calls have a hard per-server timeout so a hung server can't freeze
    a query.
  - deep_only servers are omitted from Fast mode via tools_for().
"""

import asyncio
import json
import logging
import os
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

log = logging.getLogger("lantern.mcp")


def _default_config() -> str:
    """
    Search order:
      1. MCP_CONFIG env var
      2. mcp.json next to the executable (frozen) or this file (dev)
      3. %APPDATA%/Lantern/mcp.json (or ~/.config/lantern), created with an
         empty template on first run — this is where an installed Tauri app
         keeps it, since Program Files isn't user-writable.
    """
    import sys

    base = (
        os.path.dirname(sys.executable)
        if getattr(sys, "frozen", False)
        else os.path.dirname(__file__)
    )
    local = os.path.join(base, "mcp.json")
    if os.path.exists(local):
        return local

    appdata = os.getenv("APPDATA") or os.path.join(os.path.expanduser("~"), ".config")
    cfg_dir = os.path.join(appdata, "Lantern")
    cfg = os.path.join(cfg_dir, "mcp.json")
    if not os.path.exists(cfg):
        try:
            os.makedirs(cfg_dir, exist_ok=True)
            with open(cfg, "w") as f:
                json.dump({"servers": {}}, f, indent=2)
        except OSError:
            return local  # fall back; manager treats a missing file as empty
    return cfg


CONFIG_PATH = os.getenv("MCP_CONFIG", _default_config())
TOOL_CALL_TIMEOUT = 15.0  # seconds; default per-server call timeout
DEFAULT_INIT_TIMEOUT = 20.0


class MCPManager:
    def __init__(self):
        self.sessions: dict[str, ClientSession] = {}
        self.tools: list[dict] = []  # OpenAI function format
        self.tool_owner: dict[str, str] = {}  # tool name -> server name
        self.errors: dict[str, str] = {}
        self.server_cfg: dict[str, dict] = {}  # name -> {init/call timeout, deep_only}
        self._stacks: dict[str, AsyncExitStack] = {}
        self._init_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    def _parse_spec(self, spec: dict) -> dict:
        return {
            "command": spec["command"],
            "args": spec.get("args", []),
            "env": spec.get("env", {}),
            "init_timeout": float(spec.get("init_timeout", DEFAULT_INIT_TIMEOUT)),
            "call_timeout": float(spec.get("call_timeout", TOOL_CALL_TIMEOUT)),
            "deep_only": bool(spec.get("deep_only", False)),
        }

    async def start(self):
        """Start all servers concurrently in the background; return immediately."""
        config = {}
        if os.path.exists(CONFIG_PATH):
            try:
                with open(CONFIG_PATH) as f:
                    config = json.load(f).get("servers", {})
            except Exception as e:
                self.errors["mcp.json"] = f"unreadable: {e}"

        for name, raw in config.items():
            try:
                self.server_cfg[name] = self._parse_spec(raw)
            except Exception as e:
                self.errors[name] = str(e)

        coros = [
            self._start_one(name, self.server_cfg[name])
            for name in self.server_cfg
            if name not in self.errors
        ]
        if coros:

            async def _boot():
                await asyncio.gather(*coros, return_exceptions=True)

            self._init_task = asyncio.create_task(_boot())

    async def _start_one(self, name: str, spec: dict):
        stack = AsyncExitStack()
        self._stacks[name] = stack
        try:
            params = StdioServerParameters(
                command=spec["command"],
                args=spec["args"],
                env={**os.environ, **spec["env"]},
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = await stack.enter_async_context(ClientSession(read, write))
            await asyncio.wait_for(session.initialize(), timeout=spec["init_timeout"])
            listed = await asyncio.wait_for(session.list_tools(), timeout=10)
            async with self._lock:
                self.sessions[name] = session
                for t in listed.tools:
                    # mcp<2 uses inputSchema, mcp>=2 uses input_schema
                    schema = getattr(t, "inputSchema", None) or getattr(t, "input_schema", None)
                    full = f"{name}__{t.name}"
                    self.tool_owner[full] = name
                    self.tools.append(
                        {
                            "type": "function",
                            "function": {
                                "name": full,
                                "description": (t.description or "")[:600],
                                "parameters": schema or {"type": "object", "properties": {}},
                            },
                        }
                    )
        except Exception as e:
            self.errors[name] = str(e)
            try:
                await stack.aclose()
            except Exception as close_err:
                # Best-effort teardown after a failed start; surface in debug only.
                log.debug("mcp stack close after %s failed: %s", name, close_err)
            self._stacks.pop(name, None)

    def tools_for(self, mode: str) -> list[dict]:
        """Tools allowed for this mode. deep_only servers are skipped in fast."""
        out = []
        for t in self.tools:
            owner = self.tool_owner.get(t["function"]["name"])
            if not owner or owner not in self.sessions:
                continue
            if mode == "fast" and self.server_cfg.get(owner, {}).get("deep_only"):
                continue
            out.append(t)
        return out

    async def call(self, full_name: str, arguments: dict) -> str:
        server = self.tool_owner.get(full_name)
        if not server:
            return f"Unknown tool: {full_name}"
        timeout = float(self.server_cfg.get(server, {}).get("call_timeout", TOOL_CALL_TIMEOUT))
        real_name = full_name[len(server) + 2 :]
        session = self.sessions.get(server)
        if not session:
            return f"Tool {full_name} failed: server not ready"
        try:
            result = await asyncio.wait_for(
                session.call_tool(real_name, arguments),
                timeout=timeout,
            )
            parts = []
            for c in result.content:
                if getattr(c, "type", "") == "text":
                    parts.append(c.text)
            text = "\n".join(parts) or "(tool returned no text)"
            # Tool output feeds straight into the model's context. Unbounded
            # output means unbounded prefill; cap it hard.
            words = text.split()
            if len(words) > 900:
                text = " ".join(words[:900]) + " …[truncated]"
            return text
        except TimeoutError:
            return f"Tool {full_name} timed out after {timeout}s"
        except Exception as e:
            return f"Tool {full_name} failed: {e}"

    async def stop(self):
        if self._init_task and not self._init_task.done():
            self._init_task.cancel()
            try:
                await self._init_task
            except (asyncio.CancelledError, Exception):
                pass
        for stack in list(self._stacks.values()):
            try:
                await stack.aclose()
            except Exception as e:
                # Shutdown path — process may already be dead.
                log.debug("mcp stop aclose: %s", e)
        self._stacks.clear()
        self.sessions.clear()

    def status(self) -> dict:
        return {
            "servers": list(self.sessions.keys()),
            "tools": [t["function"]["name"] for t in self.tools],
            "errors": self.errors,
        }


manager = MCPManager()

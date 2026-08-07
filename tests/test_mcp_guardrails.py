"""Guardrails for Playwright / deep_only MCP servers."""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TOY = str(Path(__file__).resolve().parent / "toy_mcp_server.py")
sys.path.insert(0, str(ROOT))

from mcp_client import MCPManager  # noqa: E402


def _spec(name="toy", **extra):
    return {
        "command": sys.executable,
        "args": [TOY],
        "env": {"TOY_NAME": name, **extra.pop("env", {})},
        **extra,
    }


async def _wait_ready(mgr: MCPManager, name: str, timeout: float = 15.0):
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if name in mgr.sessions or name in mgr.errors:
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(f"{name} still starting")


@pytest.mark.asyncio
async def test_deep_only_excluded_in_fast_included_in_deep():
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "servers": {
                        "browser": {**_spec("browser"), "deep_only": True},
                        "both": _spec("both"),
                    }
                }
            )
        )
        import mcp_client

        mcp_client.CONFIG_PATH = str(cfg)
        mgr = MCPManager()
        await mgr.start()
        try:
            await _wait_ready(mgr, "browser")
            await _wait_ready(mgr, "both")
            assert "browser" in mgr.sessions
            assert "both" in mgr.sessions

            fast = {t["function"]["name"] for t in mgr.tools_for("fast")}
            deep = {t["function"]["name"] for t in mgr.tools_for("deep")}
            assert not any(n.startswith("browser__") for n in fast)
            assert any(n.startswith("browser__") for n in deep)
            assert any(n.startswith("both__") for n in fast)
            assert any(n.startswith("both__") for n in deep)
        finally:
            await mgr.stop()


@pytest.mark.asyncio
async def test_call_timeout_returns_string_not_raise():
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "servers": {
                        "slow": {
                            **_spec("slow", env={"TOY_SLEEP": "5"}),
                            "call_timeout": 0.3,
                        }
                    }
                }
            )
        )
        import mcp_client

        mcp_client.CONFIG_PATH = str(cfg)
        mgr = MCPManager()
        await mgr.start()
        try:
            await _wait_ready(mgr, "slow")
            assert "slow" in mgr.sessions
            result = await mgr.call("slow__slow", {})
            assert "timed out" in result.lower()
            assert "0.3" in result
        finally:
            await mgr.stop()


@pytest.mark.asyncio
async def test_failing_server_does_not_block_healthy():
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "mcp.json"
        cfg.write_text(
            json.dumps(
                {
                    "servers": {
                        "bad": {
                            **_spec("bad", env={"TOY_FAIL": "1"}),
                            "init_timeout": 5,
                        },
                        "good": _spec("good"),
                    }
                }
            )
        )
        import mcp_client

        mcp_client.CONFIG_PATH = str(cfg)
        mgr = MCPManager()
        await mgr.start()
        try:
            await _wait_ready(mgr, "bad", timeout=10)
            await _wait_ready(mgr, "good", timeout=10)
            assert "bad" in mgr.errors
            assert "good" in mgr.sessions
            assert any(t["function"]["name"].startswith("good__") for t in mgr.tools)
        finally:
            await mgr.stop()

"""Speculative search + planner failure must not break /api/ask."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_TMP = tempfile.mkdtemp()
os.environ["CHATS_DB"] = str(Path(_TMP) / "chats.db")
os.environ.setdefault("MCP_CONFIG", str(Path(_TMP) / "mcp.json"))
Path(os.environ["MCP_CONFIG"]).write_text(json.dumps({"servers": {}}))

from fastapi.testclient import TestClient  # noqa: E402

import chats  # noqa: E402
import server  # noqa: E402
from planner import Plan  # noqa: E402
from server import Source  # noqa: E402

chats.DB_PATH = os.environ["CHATS_DB"]
chats.init_db()


def _read_sse(resp) -> list[tuple[str, str]]:
    events = []
    event = "message"
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((event, line.split(":", 1)[1].strip()))
            event = "message"
    return events


@pytest.fixture
def client(monkeypatch):
    captured = {"queries": [], "plans": 0}

    async def fake_search(http, query, n):
        captured["queries"].append(query)
        return [
            Source(
                n=1,
                title="France",
                url="https://example.com/france",
                snippet="Paris is the capital.",
                body="Paris is the capital of France.",
            )
        ]

    async def fake_fetch_all(http, sources):
        return None

    async def fake_stream_llm(http, messages, tools, think=False, max_tokens=None, **kwargs):
        yield ("token", "Four.")

    async def fake_plan(http, query, history_summary, tools_available, **kwargs):
        captured["plans"] += 1
        q = (query or "").strip()
        low = q.lower()
        if low in ("hi", "hello", "hey") or low == "2+2":
            intent = "compute" if "2" in low else "answer"
            return (
                Plan(intent=intent, depth="fast", format="prose", why="direct", search_query=""),
                8,
            )
        return (
            Plan(
                intent="search",
                search_query=q,
                depth=kwargs.get("user_depth", "fast"),
                format="prose",
                why="needs facts",
            ),
            8,
        )

    monkeypatch.setattr(server, "search", fake_search)
    monkeypatch.setattr(server, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(server, "stream_llm", fake_stream_llm)
    monkeypatch.setattr(server, "plan_query", fake_plan)
    monkeypatch.setattr(server.mcp, "start", AsyncMock())
    monkeypatch.setattr(server.mcp, "stop", AsyncMock())
    monkeypatch.setattr(server.mcp, "tools_for", lambda mode: [])

    with TestClient(server.app) as c:
        yield c, captured


def test_same_search_query_single_search_roundtrip(client):
    c, captured = client
    before = len(captured["queries"])
    with c.stream("GET", "/api/ask", params={"q": "capital of France", "mode": "fast"}) as r:
        events = _read_sse(r)
    # Speculative + same plan query → one search await (speculative only).
    assert len(captured["queries"]) == before + 1
    assert any(ev == "plan" for ev, _ in events)
    timing = json.loads(next(d for ev, d in events if ev == "timing"))
    assert timing["intent"] == "search"
    assert "plan_ms" in timing


def test_compute_discards_speculative_search(client):
    c, captured = client
    before = len(captured["queries"])
    with c.stream("GET", "/api/ask", params={"q": "2+2", "mode": "fast"}) as r:
        events = _read_sse(r)
    # Speculative may have started; results must not be used (no sources payload with hits).
    sources = [json.loads(d) for ev, d in events if ev == "sources"]
    assert sources and sources[0] == []
    timing = json.loads(next(d for ev, d in events if ev == "timing"))
    assert timing["intent"] == "compute"
    assert any(ev == "token" for ev, _ in events)
    # Speculative task may still have completed before cancel — at most one orphaned call.
    assert len(captured["queries"]) - before <= 1


def test_planner_failure_still_answers(client, monkeypatch):
    c, captured = client

    async def boom(*a, **k):
        raise TimeoutError("planner timeout")

    monkeypatch.setattr(server, "plan_query", boom)
    with c.stream("GET", "/api/ask", params={"q": "capital of France", "mode": "fast"}) as r:
        events = _read_sse(r)
    assert any(ev == "token" for ev, _ in events)
    plan = json.loads(next(d for ev, d in events if ev == "plan"))
    assert plan["intent"] == "search"
    assert plan.get("fallback") is True

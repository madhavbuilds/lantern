"""Heuristic routing, focus modes, health, and full-text history search."""

from __future__ import annotations

import asyncio
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
from planner import heuristic_plan  # noqa: E402

chats.DB_PATH = os.environ["CHATS_DB"]
chats.init_db()


@pytest.mark.parametrize(
    ("q", "intent", "fmt"),
    [
        ("hi", "answer", "prose"),
        ("what is 17% of 2340", "compute", "prose"),
        ("12 * (4 + 9)", "compute", "prose"),
        ("write a haiku about rain", "reason", "prose"),
        ("write a python function to dedupe a list", "reason", "code"),
        ("tauri vs electron", "search", "table"),
        ("how do I reset a git branch", "search", "steps"),
        ("top 5 sci-fi novels", "search", "list"),
        ("latest iphone price", "search", "prose"),
    ],
)
def test_heuristic_routes(q, intent, fmt):
    p = heuristic_plan(q, "fast")
    assert p.intent == intent
    assert p.format == fmt


def test_heuristic_never_skips_search_for_recent_writing():
    assert heuristic_plan("write a summary of today's news", "fast").intent == "search"


def test_heuristic_deep_keywords_raise_depth():
    assert heuristic_plan("in-depth comparison of postgres and mysql", "fast").depth == "deep"


def test_default_planner_is_instant_and_offline(monkeypatch):
    monkeypatch.delenv("LANTERN_PLANNER", raising=False)
    client = AsyncMock()
    client.post = AsyncMock(side_effect=AssertionError("no LLM call by default"))
    from planner import plan

    p, ms = asyncio.run(plan(client, "hi", "", [], model="x", llm_base="http://x"))
    assert p.intent == "answer"
    assert ms < 50


def test_focus_applies_categories_and_site_suffix(monkeypatch):
    seen = []

    class R:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "results": [
                    {"url": "https://a.com/x", "title": "A &amp; B", "content": "it&#x27;s"}
                ]
            }

    async def get(url, params=None, timeout=None):
        seen.append(params)
        return R()

    client = AsyncMock()
    client.get = get

    async def run(focus):
        server._focus.set(focus)
        return await server.search(client, "rust async", 5)

    out = asyncio.run(run("news"))
    assert seen[-1]["categories"] == "news"
    assert out[0].title == "A & B"
    assert out[0].snippet == "it's"
    asyncio.run(run("reddit"))
    assert seen[-1]["q"].endswith("site:reddit.com")
    assert "categories" not in seen[-1]


def test_history_search_ranks_and_survives_delete():
    a = chats.create_chat("Noise cancelling")
    chats.add_message(a, "user", "how do noise cancelling headphones work")
    chats.add_message(a, "assistant", "They use destructive interference [1].")
    b = chats.create_chat("Pasta")
    chats.add_message(b, "user", "best carbonara recipe")

    hits = chats.search_messages("interfering")  # porter stemming
    assert [h["chat_id"] for h in hits] == [a]
    assert "\x02" in hits[0]["snippet"]
    assert chats.search_messages("") == []
    assert chats.search_messages('"; DROP TABLE messages; --') == []

    chats.delete_chat(a)
    assert chats.search_messages("interference") == []
    assert [h["chat_id"] for h in chats.search_messages("carbonara")] == [b]


def test_health_reports_missing_model_and_search_fallback(monkeypatch):
    class Resp:
        def __init__(self, code, payload=None):
            self.status_code = code
            self._p = payload or {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError("bad")

        def json(self):
            return self._p

    async def fake_get(self, url, **kw):
        if url.endswith("/models"):
            return Resp(200, {"data": [{"id": "some-other-model"}]})
        raise ConnectionError("searxng down")

    monkeypatch.setattr(server.httpx.AsyncClient, "get", fake_get)
    monkeypatch.setattr(server.mcp, "start", AsyncMock())
    monkeypatch.setattr(server.mcp, "stop", AsyncMock())
    with TestClient(server.app) as c:
        h = c.get("/api/health").json()
    assert h["model"]["ok"] is False
    assert "ollama create" in h["model"]["fix"]
    assert h["search"]["provider"] == "duckduckgo"
    assert h["search"]["degraded"] is True

"""E2E-ish tests for follow-up search, prior-turn prompt, and chat list uniqueness."""

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
from planner import Plan, apply_sanity  # noqa: E402
from server import (  # noqa: E402
    Source,
    combine_search_query,
    last_prior_turn,
)

chats.DB_PATH = os.environ["CHATS_DB"]
chats.init_db()


def test_combine_search_query_caps_at_25_words():
    prior = " ".join(f"p{i}" for i in range(30))
    current = "and how big is it area wise"
    out = combine_search_query(prior, current, 25)
    assert len(out.split()) <= 25
    assert out.endswith(current)
    assert not out.startswith("p0")


def test_combine_search_query_first_message_unmodified():
    q = "what is the capital of France"
    assert combine_search_query(None, q) == q
    assert combine_search_query("", q) == q


def test_last_prior_turn_picks_latest_pair():
    msgs = [
        {"role": "user", "content": "old q"},
        {"role": "assistant", "content": "old a. More detail."},
        {"role": "user", "content": "what is the capital of France"},
        {
            "role": "assistant",
            "content": "Paris. It is in Europe. Extra sentence dropped.",
        },
    ]
    prior = last_prior_turn(msgs)
    assert prior is not None
    pq, pa = prior
    assert pq == "what is the capital of France"
    assert "Paris" in pa
    assert "Extra sentence dropped" not in pa


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
    captured = {"queries": [], "llm_messages": []}

    async def fake_search(http, query, n):
        captured["queries"].append(query)
        return [
            Source(
                n=1,
                title="France",
                url="https://example.com/france",
                snippet="Paris is the capital. Area 551695 km2.",
                body="Paris is the capital of France. Area 551695 km2.",
            )
        ]

    async def fake_fetch_all(http, sources):
        return None

    async def fake_stream_llm(http, messages, tools, think=False, max_tokens=None, **kwargs):
        captured["llm_messages"].append(messages)
        yield ("token", "Paris is the capital. Area about 551,695 km² [1].")

    async def fake_plan(http, query, history_summary, tools_available, **kwargs):
        q = (query or "").strip()
        low = q.lower()
        if low in ("hi", "hello", "hey"):
            return Plan(intent="answer", depth="fast", format="prose", why="greeting"), 5
        # Standalone rewrite: if we have prior history, combine like the speculative path.
        sq = q
        if history_summary and "Prior Q:" in history_summary:
            prior_q = history_summary.split("\n", 1)[0].replace("Prior Q:", "").strip()
            # Strip possible ellipsis from clip
            prior_q = prior_q.replace(" …", "")
            sq = combine_search_query(prior_q, q)
        p = Plan(
            intent="search",
            search_query=sq,
            depth=kwargs.get("user_depth", "fast"),
            format="prose",
            why="needs facts",
        )
        return apply_sanity(p, q), 5

    monkeypatch.setattr(server, "search", fake_search)
    monkeypatch.setattr(server, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(server, "stream_llm", fake_stream_llm)
    monkeypatch.setattr(server, "plan_query", fake_plan)
    monkeypatch.setattr(server.mcp, "start", AsyncMock())
    monkeypatch.setattr(server.mcp, "stop", AsyncMock())
    monkeypatch.setattr(server.mcp, "tools_for", lambda mode: [])

    with TestClient(server.app) as c:
        yield c, captured


def test_followup_search_contains_france_and_prior_in_prompt(client):
    c, captured = client

    with c.stream(
        "GET", "/api/ask", params={"q": "what is the capital of France", "mode": "fast"}
    ) as r:
        events = _read_sse(r)
    assert any(ev == "chat" for ev, _ in events)
    chat_data = json.loads(next(data for ev, data in events if ev == "chat"))
    chat_id = chat_data["id"]
    assert "France" in captured["queries"][-1]
    first_msgs = captured["llm_messages"][-1]
    roles = [m["role"] for m in first_msgs]
    assert roles == ["system", "user"]

    with c.stream(
        "GET",
        "/api/ask",
        params={
            "q": "and how big is it area wise",
            "mode": "fast",
            "chat_id": chat_id,
        },
    ) as r:
        _read_sse(r)

    assert "France" in captured["queries"][-1]
    assert "and how big is it area wise" in captured["queries"][-1]
    assert len(captured["queries"][-1].split()) <= 25

    second_msgs = captured["llm_messages"][-1]
    assert second_msgs[1]["role"] == "user"
    user_content = second_msgs[1]["content"]
    assert "France" in user_content
    assert "<conversation>" in user_content
    assert "and how big is it area wise" in user_content
    assert [m["role"] for m in second_msgs] == ["system", "user"]


def test_chats_list_has_no_duplicate_ids(client):
    c, _ = client
    with c.stream(
        "GET", "/api/ask", params={"q": "unique factual query about France", "mode": "fast"}
    ) as r:
        events = _read_sse(r)
    chat_id = json.loads(next(data for ev, data in events if ev == "chat"))["id"]

    with c.stream(
        "GET",
        "/api/ask",
        params={"q": "follow up about France", "mode": "fast", "chat_id": chat_id},
    ) as r:
        _read_sse(r)

    rows = c.get("/api/chats").json()
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))
    assert chat_id in ids


def test_answer_intent_skips_search(client):
    c, captured = client
    before = len(captured["queries"])
    with c.stream("GET", "/api/ask", params={"q": "Hi", "mode": "fast"}) as r:
        events = _read_sse(r)
    # Speculative search may have fired before plan; must not surface sources.
    sources_ev = [json.loads(d) for ev, d in events if ev == "sources"]
    assert sources_ev and sources_ev[0] == []
    assert any(ev == "token" for ev, _ in events)
    timing = json.loads(next(data for ev, data in events if ev == "timing"))
    assert timing["intent"] == "answer"
    assert timing["route"] == "answer"
    plan = json.loads(next(d for ev, d in events if ev == "plan"))
    assert plan["intent"] == "answer"
    assert len(captured["queries"]) - before <= 1


def test_force_search_overrides_planner(client):
    c, captured = client
    before = len(captured["queries"])
    with c.stream("GET", "/api/ask", params={"q": "Hi", "mode": "fast", "force": "search"}) as r:
        events = _read_sse(r)
    assert len(captured["queries"]) >= before + 1
    timing = json.loads(next(data for ev, data in events if ev == "timing"))
    assert timing["intent"] == "search"
    plan = json.loads(next(d for ev, d in events if ev == "plan"))
    assert plan["intent"] == "search"

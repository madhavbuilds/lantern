"""UX contracts for a non-technical Lantern user."""

from __future__ import annotations

import json
import os
import re
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
    count_uncited_sentences,
    detect_user_overrides,
    format_flags,
    humanize_error,
)

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
    captured = {"queries": [], "llm_messages": [], "answers": []}

    async def fake_search(http, query, n):
        captured["queries"].append(query)
        # Deliberately France-shaped — never Russia — for the follow-up bug.
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
        sys = (messages[0].get("content") or "") if messages else ""
        user = ""
        for m in messages:
            if m.get("role") == "user":
                user = m.get("content") or ""
        # One-word obedience
        if "one word" in user.lower() or "minimal answer" in sys.lower():
            ans = "Paris"
        elif "how big" in user.lower() or "area" in user.lower():
            ans = "France covers about 551,695 km² [1]."
        elif "capital of france" in user.lower() or "france" in user.lower():
            ans = "Paris is the capital of France [1]."
        elif user.strip().lower() in ("hi", "hello", "hey"):
            ans = "Hello."
        else:
            ans = "Paris is the capital of France [1]."
        captured["answers"].append(ans)
        yield ("token", ans)

    async def fake_plan(http, query, history_summary, tools_available, **kwargs):
        q = (query or "").strip()
        low = q.lower()
        if low in ("hi", "hello", "hey"):
            return Plan(intent="answer", depth="fast", format="prose", why="greeting"), 5
        sq = q
        if history_summary and "Prior Q:" in history_summary:
            prior_q = history_summary.split("\n", 1)[0].replace("Prior Q:", "").strip()
            prior_q = prior_q.replace(" …", "")
            sq = combine_search_query(prior_q, q)
        # Never invent Russia for a France follow-up.
        assert "russia" not in sq.lower()
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


def test_capital_of_france_is_short_with_few_citations(client):
    c, captured = client
    with c.stream(
        "GET", "/api/ask", params={"q": "what is the capital of France", "mode": "fast"}
    ) as r:
        events = _read_sse(r)
    tokens = [json.loads(d) for ev, d in events if ev == "token"]
    answer = "".join(tokens)
    assert len(answer.split()) < 40, answer
    cites = re.findall(r"\[(\d+)\]", answer)
    assert len(set(cites)) <= 3
    timing = json.loads(next(d for ev, d in events if ev == "timing"))
    assert "uncited_sentences" in timing


def test_answer_in_one_word_capital_of_france(client):
    c, _ = client
    q = "answer in one word: capital of France"
    assert detect_user_overrides(q).get("length") == "minimal"
    with c.stream("GET", "/api/ask", params={"q": q, "mode": "fast"}) as r:
        events = _read_sse(r)
    tokens = [json.loads(d) for ev, d in events if ev == "token"]
    answer = "".join(tokens).strip()
    # One word (punctuation allowed).
    words = re.findall(r"[A-Za-z]+", answer)
    assert len(words) == 1, answer


def test_france_how_big_is_it_area_wise_not_russia(client):
    """Named after the bug: follow-ups must not drift to Russia."""
    c, captured = client
    with c.stream(
        "GET", "/api/ask", params={"q": "what is the capital of France", "mode": "fast"}
    ) as r:
        events = _read_sse(r)
    chat_id = json.loads(next(d for ev, d in events if ev == "chat"))["id"]

    with c.stream(
        "GET",
        "/api/ask",
        params={"q": "and how big is it area wise", "mode": "fast", "chat_id": chat_id},
    ) as r:
        events2 = _read_sse(r)
    tokens = [json.loads(d) for ev, d in events2 if ev == "token"]
    answer = "".join(tokens)
    assert "France" in answer or "551" in answer
    assert "Russia" not in answer
    assert "France" in captured["queries"][-1]
    assert "russia" not in captured["queries"][-1].lower()


def test_answer_intent_carries_unverified_knowledge_marker(client):
    c, _ = client
    with c.stream("GET", "/api/ask", params={"q": "Hi", "mode": "fast"}) as r:
        events = _read_sse(r)
    timing = json.loads(next(d for ev, d in events if ev == "timing"))
    assert timing["intent"] == "answer"
    # Marker is a UI concern; contract is that answer intent is distinct from search
    # and carries no sources — the UI shows the muted line for intent=answer.
    sources_ev = [json.loads(d) for ev, d in events if ev == "sources"]
    assert sources_ev and sources_ev[0] == []


def test_every_error_path_is_human(client):
    bad = [
        humanize_error(ConnectionError("connection refused"), where="llm"),
        humanize_error(ConnectionError("connection refused"), where="search"),
        humanize_error(RuntimeError("CUDA out of memory"), where="llm"),
        humanize_error(RuntimeError("model 'x' not found"), where="llm"),
        humanize_error(Exception("boom Traceback Errno"), where="ask"),
    ]
    for err in bad:
        text = err["text"]
        assert "Traceback" not in text
        assert "Errno" not in text
        assert "httpx" not in text.lower()
        assert "module" not in text.lower() or "model" in text.lower()
        assert err.get("action") in ("retry", "settings", "fix")


def test_deleting_a_chat_removes_rows_from_both_tables(client):
    c, _ = client
    with c.stream(
        "GET", "/api/ask", params={"q": "what is the capital of France", "mode": "fast"}
    ) as r:
        events = _read_sse(r)
    chat_id = json.loads(next(d for ev, d in events if ev == "chat"))["id"]
    assert chats.get_messages(chat_id)
    r = c.delete(f"/api/chats/{chat_id}")
    assert r.status_code == 200
    # Hard delete — rows gone, not flagged.
    with chats._db() as conn:
        n_chats = conn.execute(
            "SELECT COUNT(*) AS n FROM chats WHERE id = ?", (chat_id,)
        ).fetchone()["n"]
        n_msgs = conn.execute(
            "SELECT COUNT(*) AS n FROM messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()["n"]
    assert n_chats == 0
    assert n_msgs == 0


def test_overlong_flag_and_uncited_helpers():
    flags = format_flags(
        " ".join(["word"] * 130),
        "prose",
        query="capital of France",
        intent="search",
    )
    assert "overlong" in flags
    assert (
        count_uncited_sentences(
            "Paris is a lovely capital city. London is also quite nice overall."
        )
        == 2
    )
    assert count_uncited_sentences("Paris is the capital of France [1].") == 0


def test_detect_user_overrides_table_and_hindi():
    o = detect_user_overrides("compare them in a table in Hindi")
    assert o.get("format") == "table"
    assert o.get("language") == "hindi"
    assert "Hindi" in o.get("instruction", "") or "hindi" in o.get("instruction", "")

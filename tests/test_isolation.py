"""Strict per-session isolation — chats never see each other's messages."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_TMP = tempfile.mkdtemp()
os.environ["CHATS_DB"] = str(Path(_TMP) / "isolation.db")
os.environ.setdefault("MCP_CONFIG", str(Path(_TMP) / "mcp.json"))
Path(os.environ["MCP_CONFIG"]).write_text(json.dumps({"servers": {}}))

from fastapi.testclient import TestClient  # noqa: E402

import chats  # noqa: E402
import server  # noqa: E402
from planner import Plan, apply_sanity  # noqa: E402
from server import Source, short_term_history  # noqa: E402

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


@pytest.fixture(autouse=True)
def _clean():
    chats.wipe_all()
    yield
    chats.wipe_all()


@pytest.fixture
def client(monkeypatch):
    captured = {"llm_messages": []}

    async def fake_search(http, query, n):
        return [
            Source(
                n=1,
                title="GPU",
                url="https://example.com/gpu",
                snippet="VRAM notes.",
                body="VRAM and unified memory for local models.",
            )
        ]

    async def fake_fetch_all(http, sources):
        return None

    async def fake_stream_llm(http, messages, tools, think=False, max_tokens=None, **kwargs):
        captured["llm_messages"].append(messages)
        yield ("token", "Here is an answer.")

    async def fake_plan(http, query, history_summary, tools_available, **kwargs):
        p = Plan(
            intent="search",
            search_query=query,
            depth="fast",
            format="prose",
            why="needs facts",
        )
        return apply_sanity(p, query), 5

    monkeypatch.setattr(server, "search", fake_search)
    monkeypatch.setattr(server, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(server, "stream_llm", fake_stream_llm)
    monkeypatch.setattr(server, "plan_query", fake_plan)
    monkeypatch.setattr(server.mcp, "start", AsyncMock())
    monkeypatch.setattr(server.mcp, "stop", AsyncMock())
    monkeypatch.setattr(server.mcp, "tools_for", lambda mode: [])

    with TestClient(server.app) as c:
        yield c, captured


def test_new_chat_sees_nothing_from_prior_chat(client):
    """The most important test in the repo: no cross-chat leakage."""
    c, captured = client

    with c.stream(
        "GET",
        "/api/ask",
        params={
            "q": "How much VRAM does Ollama need for qwen3 8b on Apple Silicon?",
            "mode": "fast",
        },
    ) as r:
        events = _read_sse(r)
    assert any(ev == "token" for ev, _ in events)

    # Brand-new chat asking a related question
    captured["llm_messages"].clear()
    with c.stream(
        "GET",
        "/api/ask",
        params={
            "q": "What VRAM should I leave free for local LLMs?",
            "mode": "fast",
        },
    ) as r:
        _read_sse(r)

    blob = json.dumps(captured["llm_messages"][-1]).lower()
    # Chat A's distinctive question must not leak into chat B's prompt
    assert "how much vram does ollama need" not in blob
    assert "qwen3 8b" not in blob


def test_project_instructions_not_sibling_messages(client):
    c, captured = client
    pid = chats.create_project("Lab", "Always prefer Q4 quantization.")
    a = chats.create_chat("VRAM A", project_id=pid)
    chats.add_message(a, "user", "SECRET_SIBLING_TOKEN about VRAM budgets")
    chats.add_message(a, "assistant", "Reply mentioning SECRET_SIBLING_TOKEN", route="search")

    with c.stream(
        "GET",
        "/api/ask",
        params={
            "q": "Recommend a model size for my Mac",
            "mode": "fast",
            "project_id": pid,
        },
    ) as r:
        _read_sse(r)

    msgs = captured["llm_messages"][-1]
    system = msgs[0]["content"]
    user = msgs[-1]["content"]
    assert "Q4 quantization" in system
    assert "SECRET_SIBLING_TOKEN" not in system
    assert "SECRET_SIBLING_TOKEN" not in user
    assert "<project_instructions>" in system


def test_delete_project_keeps_chats():
    pid = chats.create_project("Temp", "instr")
    cid = chats.create_chat("Keep me", project_id=pid)
    assert chats.delete_project(pid) is True
    row = chats.get_chat(cid)
    assert row is not None
    assert row["project_id"] is None
    assert chats.get_project(pid) is None


def test_about_empty_no_user_context_block(client):
    c, captured = client
    chats.set_about("", enabled=True)
    with c.stream(
        "GET", "/api/ask", params={"q": "what is 2+2 arithmetic please", "mode": "fast"}
    ) as r:
        _read_sse(r)
    system = captured["llm_messages"][-1][0]["content"]
    assert "<user_context>\n" not in system


def test_about_over_300_tokens_truncated():
    huge = "word " * 500
    out = chats.set_about(huge, enabled=True)
    assert out["tokens"] <= chats.ABOUT_TOKEN_CAP
    prompt = chats.about_for_prompt()
    assert chats.est_tokens(prompt) <= chats.ABOUT_TOKEN_CAP
    assert prompt.endswith("…") or chats.est_tokens(huge) <= chats.ABOUT_TOKEN_CAP


def test_in_chat_history_never_exceeds_250_tokens():
    msgs = []
    for i in range(20):
        msgs.append(
            {
                "role": "user",
                "content": f"question {i} " + ("detail " * 40),
            }
        )
        msgs.append(
            {
                "role": "assistant",
                "content": f"Answer {i}. Second sentence. Third drops.",
                "route": "search",
            }
        )
    hist = short_term_history(msgs)
    total = sum(server.est_tokens(q) + server.est_tokens(a) for q, a in hist)
    assert len(hist) <= server.SHORT_TERM_TURNS
    assert total <= server.SHORT_TERM_TOKENS + 5

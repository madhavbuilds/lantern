"""Streaming lifecycle: cancel frees the model, stopped persists, chats don't cross."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

_TMP = tempfile.mkdtemp()
os.environ["CHATS_DB"] = str(Path(_TMP) / "chats.db")
os.environ.setdefault("MCP_CONFIG", str(Path(_TMP) / "mcp.json"))
Path(os.environ["MCP_CONFIG"]).write_text(json.dumps({"servers": {}}))

from fastapi.testclient import TestClient  # noqa: E402

import chats  # noqa: E402
import server  # noqa: E402
from planner import Plan, apply_sanity  # noqa: E402
from server import Source  # noqa: E402

chats.DB_PATH = os.environ["CHATS_DB"]
chats.init_db()


def _read_sse_lines(resp, *, max_events=200):
    events = []
    event = "message"
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            events.append((event, line.split(":", 1)[1].strip()))
            event = "message"
            if len(events) >= max_events:
                break
    return events


@pytest.fixture
def base_patches(monkeypatch):
    async def fake_search(http, query, n):
        return [
            Source(
                n=1,
                title="Doc",
                url="https://example.com/doc",
                snippet="A fact.",
                body="A fact about the topic.",
            )
        ]

    async def fake_fetch_all(http, sources):
        return None

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
    monkeypatch.setattr(server, "plan_query", fake_plan)
    monkeypatch.setattr(server.mcp, "start", AsyncMock())
    monkeypatch.setattr(server.mcp, "stop", AsyncMock())
    monkeypatch.setattr(server.mcp, "tools_for", lambda mode: [])


def test_cancel_closes_upstream_llm_stream(base_patches, monkeypatch):
    """Client disconnect / cancel must aclose the httpx LLM stream — not run to end."""
    closed = {"n": 0}
    lines_sent = {"n": 0}
    total_lines = 40

    class FakeResp:
        def __init__(self):
            self._closed = False

        async def aiter_lines(self):
            for i in range(total_lines):
                if self._closed:
                    return
                lines_sent["n"] += 1
                await asyncio.sleep(0.02)
                payload = json.dumps({"choices": [{"delta": {"content": f"t{i} "}}]})
                yield f"data: {payload}"
            yield "data: [DONE]"

        async def aclose(self):
            self._closed = True
            closed["n"] += 1

    fake_resp = FakeResp()

    class FakeClient:
        def build_request(self, *a, **k):
            return httpx.Request("POST", "http://llm.test/v1/chat/completions")

        async def send(self, req, stream=True, timeout=None):
            return fake_resp

    cancel = asyncio.Event()

    async def run():
        client = FakeClient()
        tokens = []
        agen = server.stream_llm(
            client,
            [{"role": "user", "content": "hi"}],
            None,
            cancel=cancel,
        )

        async def stopper():
            await asyncio.sleep(0.08)
            cancel.set()

        t = asyncio.create_task(stopper())
        async for kind, val in agen:
            if kind == "token":
                tokens.append(val)
        await t
        return tokens

    tokens = asyncio.run(run())
    assert closed["n"] >= 1, "upstream httpx stream must be aclose()'d on cancel"
    assert lines_sent["n"] < total_lines, "must not drain the full LLM stream after cancel"
    assert len(tokens) < total_lines


def test_stopped_stream_persists_partial(base_patches, monkeypatch):
    """When cancel fires mid-generation, the partial answer is saved with stopped=1."""

    async def slow_stream_llm(http, messages, tools, think=False, max_tokens=None, **kwargs):
        cancel = kwargs.get("cancel")
        yield ("token", "Partial answer that was cut")
        # Simulate client stop / disconnect — ASGITransport does not interleave
        # a concurrent DELETE with the streaming generator, so we trip cancel here.
        if cancel:
            cancel.set()
        return

    monkeypatch.setattr(server, "stream_llm", slow_stream_llm)

    with TestClient(server.app) as c:
        with c.stream("GET", "/api/ask", params={"q": "long question", "mode": "fast"}) as r:
            events = _read_sse_lines(r)
        assert any(ev == "stopped" for ev, _ in events), events
        tokens = [json.loads(data) for ev, data in events if ev == "token"]
        assert tokens == ["Partial answer that was cut"]
        chat = json.loads(next(data for ev, data in events if ev == "chat"))
        msgs = chats.get_messages(chat["id"])
        asst = [m for m in msgs if m["role"] == "assistant"]
        assert asst and asst[-1]["stopped"] is True
        assert asst[-1]["content"] == "Partial answer that was cut"


def test_delete_ask_sets_cancel_event(base_patches):
    ev = asyncio.Event()
    rid = "testdelete001"
    server._active_asks[rid] = {"cancel": ev}
    with TestClient(server.app) as c:
        r = c.delete(f"/api/ask/{rid}")
    assert r.status_code == 200
    assert ev.is_set()
    server._active_asks.pop(rid, None)


def test_two_concurrent_streams_do_not_cross(base_patches, monkeypatch):
    async def tagged_stream_llm(http, messages, tools, think=False, max_tokens=None, **kwargs):
        # Tag tokens by the user question so buffers can't silently swap.
        q = ""
        for m in messages:
            if m.get("role") == "user":
                q = m.get("content") or ""
        tag = "ALPHA" if "alpha-topic" in q else "BETA" if "beta-topic" in q else "X"
        for ch in f"{tag}-ok ":
            yield ("token", ch)
            await asyncio.sleep(0.01)

    monkeypatch.setattr(server, "stream_llm", tagged_stream_llm)

    chat_a = chats.create_chat("A")
    chat_b = chats.create_chat("B")
    results = {}

    def run(label, chat_id, q):
        with TestClient(server.app) as c:
            with c.stream(
                "GET",
                "/api/ask",
                params={"q": q, "mode": "fast", "chat_id": chat_id},
            ) as r:
                buf = []
                event = "message"
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        event = line.split(":", 1)[1].strip()
                    elif line.startswith("data:"):
                        data = line.split(":", 1)[1].strip()
                        if event == "token":
                            buf.append(json.loads(data))
                        event = "message"
                results[label] = "".join(buf)

    t1 = threading.Thread(target=run, args=("a", chat_a, "tell me about alpha-topic please"))
    t2 = threading.Thread(target=run, args=("b", chat_b, "tell me about beta-topic please"))
    t1.start()
    t2.start()
    t1.join(timeout=30)
    t2.join(timeout=30)

    assert "ALPHA" in results["a"] and "BETA" not in results["a"]
    assert "BETA" in results["b"] and "ALPHA" not in results["b"]

    msgs_a = chats.get_messages(chat_a)
    msgs_b = chats.get_messages(chat_b)
    assert any("ALPHA" in (m["content"] or "") for m in msgs_a if m["role"] == "assistant")
    assert any("BETA" in (m["content"] or "") for m in msgs_b if m["role"] == "assistant")


def test_client_caps_concurrent_streams_at_two():
    """
    Concurrent cap is enforced in ui/stream.js (MAX_STREAMS=2 + queue).
    Local models serialize work — a 3rd in-flight ask just makes everything slow,
    so the UI queues with "Waiting for the model…".
    """
    src = Path(__file__).resolve().parents[1] / "ui" / "stream.js"
    text = src.read_text()
    assert "MAX_STREAMS = 2" in text
    assert "queue" in text
    assert (
        "Waiting for a free slot"
        in (Path(__file__).resolve().parents[1] / "ui" / "index.html").read_text()
    )

"""Validation-layer tests for the planner (not model judgement)."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx

from planner import (
    Plan,
    apply_sanity,
    fallback_plan,
    filter_tools,
    forced_plan,
    parse_plan_json,
    plan,
)


def test_malformed_json_falls_back():
    p = parse_plan_json("not json at all", "what is tar", "fast")
    assert p.intent == "search"
    assert p.fallback is True
    assert p.format == "prose"


def test_unknown_intent_falls_back():
    p = parse_plan_json(
        '{"intent":"dance","depth":"fast","format":"prose","why":"x"}', "hi", "fast"
    )
    assert p.intent in ("search", "answer")  # "hi" may sanity→answer after fallback search
    assert p.fallback is True or p.intent == "answer"


def test_unknown_intent_on_factual_falls_back_to_search():
    p = parse_plan_json(
        '{"intent":"dance","depth":"fast","format":"prose","why":"x"}',
        "capital of France",
        "deep",
    )
    assert p.intent == "search"
    assert p.fallback is True
    assert p.depth == "deep"


def test_sanity_answer_plus_latest_price_forced_search():
    p = apply_sanity(
        Plan(intent="answer", format="prose", why="known", depth="fast"),
        "latest iPhone price",
    )
    assert p.intent == "search"


def test_sanity_search_plus_hi_forced_answer():
    p = apply_sanity(
        Plan(intent="search", search_query="hi", format="prose", why="x", depth="fast"),
        "hi",
    )
    assert p.intent == "answer"


def test_chart_with_answer_intent_demoted_to_prose():
    p = apply_sanity(
        Plan(intent="answer", format="chart", why="viz", depth="fast"),
        "hello there",
    )
    assert p.format == "prose"
    assert p.intent == "answer"


def test_deep_only_tool_dropped_when_fast():
    p = Plan(
        intent="search",
        search_query="open example.com",
        depth="fast",
        format="prose",
        why="browse",
        tools=["playwright__browser_navigate", "filesystem__read_file"],
    )
    available = [
        {"name": "playwright__browser_navigate", "description": "go"},
        {"name": "filesystem__read_file", "description": "read"},
    ]
    p = filter_tools(p, available, deep_only_servers={"playwright"})
    assert "playwright__browser_navigate" in p.tools_dropped
    assert "filesystem__read_file" in p.tools
    assert "playwright__browser_navigate" not in p.tools


def test_planner_timeout_returns_fallback():
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))

    async def run():
        return await plan(
            client,
            "capital of France",
            "",
            [],
            model="x",
            user_depth="fast",
            llm_base="http://127.0.0.1:9/v1",
        )

    p, ms = asyncio.run(run())
    assert p.intent == "search"
    assert p.fallback is True
    assert "timeout" in p.why


def test_planner_http_error_fallback():
    client = AsyncMock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("down"))

    async def run():
        return await plan(
            client,
            "capital of France",
            "",
            [],
            model="x",
            user_depth="fast",
            llm_base="http://127.0.0.1:9/v1",
        )

    p, _ = asyncio.run(run())
    assert p.intent == "search"
    assert p.fallback is True


def test_forced_plan_chat_alias():
    p = forced_plan("chat", "hi", "fast")
    assert p.intent == "answer"


def test_fallback_greeting_becomes_answer():
    p = fallback_plan("hi", "fast", "planner timeout")
    assert p.intent == "answer"

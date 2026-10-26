"""
Model-driven query planner for Lantern.

One short non-streaming LLM call returns a Plan. Validation + sanity overrides
live here so a small model can't wreck the experience. A failed plan never
fails the query — callers always get a usable Plan.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

import httpx

log = logging.getLogger("lantern.planner")

PLANNER_TIMEOUT = 4.0  # seconds; cancel and fall back
PLANNER_MAX_TOKENS = 120

INTENTS = frozenset({"answer", "search", "compute", "reason"})
DEPTHS = frozenset({"fast", "deep"})
FORMATS = frozenset({"prose", "table", "chart", "code", "list", "steps"})

_GREETINGS = frozenset(
    {
        "hi",
        "hello",
        "hey",
        "yo",
        "namaste",
        "नमस्ते",
        "thanks",
        "thank you",
        "thx",
        "ok",
        "okay",
        "cool",
        "nice",
        "bye",
        "good morning",
        "good night",
        "good evening",
        "good afternoon",
        "how are you",
        "who are you",
        "what can you do",
    }
)

_RECENCY = re.compile(
    r"\b(current|latest|today|now|tonight|this week|this month|202[4-9]|203\d)\b",
    re.I,
)
_PRICE = re.compile(r"\b(price|cost|\$|€|£|usd|eur|inr)\b", re.I)

PLANNER_SYSTEM = """\
You classify one user question for Lantern. Reply with ONLY a JSON object, no markdown.

Fields:
{"intent":"answer|search|compute|reason","search_query":"","depth":"fast|deep","format":"prose|table|chart|code|list|steps","why":"≤8 words","tools":[]}

intent:
- answer: known fact, social, or transform of prior turn. No retrieval.
- search: needs current/external facts (now, prices, versions, events, real products). Dominant need wins: "compare Tauri vs Electron" → search.
- compute: arithmetic, dates, conversions, list generation, statistics over a defined range.
- reason: multi-step logic, planning, debugging, trade-offs, writing. Needs thinking, not facts.

format: prose (default); table (3+ items × 2+ attrs); chart (3+ numeric points, shared unit); code (answer IS code / huge result); list; steps (ordered procedure).

depth: deep only if pages must be read; else fast.
search_query: only for search — standalone, searchable rewrite.
tools: optional subset of available tool names; empty if none needed.
"""


@dataclass
class Plan:
    intent: str = "search"
    search_query: str = ""
    depth: str = "fast"
    format: str = "prose"
    why: str = "fallback"
    tools: list[str] = field(default_factory=list)
    tools_dropped: list[str] = field(default_factory=list)
    fallback: bool = False

    def to_sse(self) -> dict:
        d = {
            "intent": self.intent,
            "depth": self.depth,
            "format": self.format,
            "why": self.why,
        }
        if self.intent == "search" and self.search_query:
            d["search_query"] = self.search_query
        if self.tools:
            d["tools"] = self.tools
        if self.tools_dropped:
            d["tools_dropped"] = self.tools_dropped
        if self.fallback:
            d["fallback"] = True
        return d


_ARITH = re.compile(r"^[\s\d().,+\-*/^%x×÷=?]+$")
_COMPUTE = re.compile(
    r"\b(calculate|compute|convert|how many (days|hours|minutes|weeks|seconds) "
    r"(between|until|since|in)|what is \d[\d\s.,]*\s*(\+|-|\*|/|x|×|÷|%|percent|times|"
    r"plus|minus|divided)|\d+\s*% of \d+|square root|factorial|prime numbers)\b",
    re.I,
)
_WRITE = re.compile(
    r"^(write|draft|compose|rewrite|rephrase|proofread|translate|brainstorm|"
    r"summari[sz]e (this|the following|my)|make (this|it) (shorter|longer|better)|"
    r"fix (this|my)|debug|refactor|generate (a|an|some)|create (a|an) "
    r"(poem|story|email|essay|plan|outline|function|script))\b",
    re.I,
)
_CODE_ASK = re.compile(
    r"\b(write|give me|show me|generate)\b.*\b(function|script|regex|sql|query|"
    r"snippet|class|program|code)\b",
    re.I,
)
_STEPS = re.compile(r"^(how (do|can|to|should) (i|you|we)|how to|steps to|guide to)\b", re.I)
_COMPARE = re.compile(r"\b(vs\.?|versus|compare|comparison|difference between)\b", re.I)
_LIST = re.compile(r"\b(top \d+|best \d+|list of|examples of|\d+ (ways|ideas|tips))\b", re.I)
_DEEP = re.compile(
    r"\b(in depth|in-depth|detailed|thorough|research|deep dive|comprehensive)\b", re.I
)


def heuristic_plan(query: str, user_depth: str = "fast") -> Plan:
    """Instant, deterministic routing — no model round trip.

    Thinking models (qwen3 2507 and friends) reason before emitting JSON, so
    an LLM planner either blows its time budget or truncates. Rules cover the
    common shapes; anything ambiguous goes to search, which is always safe.
    """
    q = (query or "").strip()
    depth = user_depth if user_depth in DEPTHS else "fast"
    if _DEEP.search(q):
        depth = "deep"

    fmt = "prose"
    if _CODE_ASK.search(q):
        fmt = "code"
    elif _COMPARE.search(q):
        fmt = "table"
    elif _STEPS.search(q):
        fmt = "steps"
    elif _LIST.search(q):
        fmt = "list"

    if _is_greeting(q):
        p = Plan(intent="answer", format="prose", why="greeting")
    elif (_ARITH.match(q) and re.search(r"\d", q)) or _COMPUTE.search(q):
        p = Plan(intent="compute", format="prose", why="calculation")
    elif (_WRITE.search(q) or fmt == "code") and not _RECENCY.search(q):
        p = Plan(intent="reason", format=fmt, why="writing or reasoning task")
    else:
        p = Plan(intent="search", search_query=q, format=fmt, why="looked it up")
    p.depth = depth
    return apply_sanity(p, q)


def fallback_plan(query: str, user_depth: str = "fast", why: str = "planner fallback") -> Plan:
    p = Plan(
        intent="search",
        search_query=(query or "").strip(),
        depth=user_depth if user_depth in DEPTHS else "fast",
        format="prose",
        why=why[:80] or "planner fallback",
        fallback=True,
    )
    return apply_sanity(p, query)


def _word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w])


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower().strip())


def _is_greeting(query: str) -> bool:
    n = _norm(query)
    if not n or _word_count(n) > 3:
        return False
    if n in _GREETINGS:
        return True
    lead = n.split()[0] if n else ""
    return lead in {"hi", "hello", "hey", "yo", "thanks", "thx", "ok", "okay", "bye"}


def apply_sanity(plan: Plan, query: str, *, honor_force: bool = False) -> Plan:
    """Deterministic overrides — never trust the model alone.

    honor_force=True skips the greeting→answer demotion so a user "Search
    instead" click still searches even on "hi".
    """
    q = query or ""
    if plan.intent == "answer" and (_RECENCY.search(q) or _PRICE.search(q)):
        plan.intent = "search"
        plan.search_query = plan.search_query or q.strip()
        plan.why = "needs live facts"

    if not honor_force and plan.intent == "search" and _is_greeting(q):
        plan.intent = "answer"
        plan.search_query = ""
        plan.format = "prose"
        plan.why = "greeting"
        plan.tools = []

    if plan.format == "chart" and plan.intent not in ("search", "compute"):
        plan.format = "prose"

    if plan.intent != "search":
        plan.search_query = ""
    elif not (plan.search_query or "").strip():
        plan.search_query = q.strip()

    if plan.depth not in DEPTHS:
        plan.depth = "fast"
    if plan.format not in FORMATS:
        plan.format = "prose"
    if plan.intent not in INTENTS:
        return fallback_plan(q, plan.depth, "unknown intent")

    plan.why = " ".join((plan.why or "").split()[:8]) or "planned"
    return plan


def filter_tools(
    plan: Plan,
    available: list[dict],
    deep_only_servers: set[str] | None = None,
) -> Plan:
    """Keep only named tools that exist; drop deep_only when depth=fast."""
    deep_only_servers = deep_only_servers or set()
    by_name = {t.get("name"): t for t in available if t.get("name")}
    kept, dropped = [], []
    for name in plan.tools or []:
        if name not in by_name:
            dropped.append(name)
            continue
        server = name.split("__", 1)[0] if "__" in name else ""
        if plan.depth == "fast" and server in deep_only_servers:
            dropped.append(name)
            continue
        kept.append(name)
    plan.tools = kept
    plan.tools_dropped = dropped
    return plan


def parse_plan_json(raw: str, query: str, user_depth: str) -> Plan:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        log.warning("planner: no JSON object in response")
        return fallback_plan(query, user_depth, "malformed plan")
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        log.warning("planner: JSON decode failed")
        return fallback_plan(query, user_depth, "malformed plan")

    intent = data.get("intent")
    if intent not in INTENTS:
        log.warning("planner: unknown intent %r", intent)
        return fallback_plan(query, user_depth, "unknown intent")

    depth = data.get("depth", user_depth)
    if depth not in DEPTHS:
        depth = user_depth if user_depth in DEPTHS else "fast"
    fmt = data.get("format", "prose")
    if fmt not in FORMATS:
        fmt = "prose"

    tools = data.get("tools") or []
    if not isinstance(tools, list):
        tools = []
    tools = [str(t) for t in tools if t]

    plan = Plan(
        intent=intent,
        search_query=str(data.get("search_query") or "").strip(),
        depth=depth,
        format=fmt,
        why=str(data.get("why") or "planned"),
        tools=tools,
    )
    return apply_sanity(plan, query)


def merge_depth(user_depth: str, plan_depth: str) -> str:
    """User Deep is a floor; planner may raise fast→deep, never lower deep→fast."""
    if user_depth == "deep":
        return "deep"
    return plan_depth if plan_depth in DEPTHS else "fast"


def forced_plan(force: str, query: str, user_depth: str) -> Plan:
    """User override from the UI (Search instead / Compute instead / …)."""
    intent = {"chat": "answer"}.get(force, force)
    if intent not in INTENTS:
        intent = "search"
    return apply_sanity(
        Plan(
            intent=intent,
            search_query=query.strip() if intent == "search" else "",
            depth=user_depth if user_depth in DEPTHS else "fast",
            format="prose",
            why="forced by user",
            tools=[],
        ),
        query,
        honor_force=True,
    )


async def plan(
    client: httpx.AsyncClient,
    query: str,
    history_summary: str,
    tools_available: list[dict],
    *,
    model: str,
    user_depth: str = "fast",
    llm_base: str,
    llm_key: str = "not-needed",
) -> tuple[Plan, int]:
    """
    Returns (Plan, plan_ms). Default is the instant heuristic router; set
    LANTERN_PLANNER=llm (optionally with a small non-thinking PLANNER_MODEL)
    for one non-streaming classification call instead.
    On timeout / error / bad JSON → fallback search plan.
    """
    t0 = time.perf_counter()
    if os.getenv("LANTERN_PLANNER", "heuristic").lower() != "llm":
        return heuristic_plan(query, user_depth), int((time.perf_counter() - t0) * 1000)
    use_model = os.getenv("PLANNER_MODEL") or model

    tool_lines = []
    for t in tools_available[:40]:
        name = t.get("name") or ""
        desc = (t.get("description") or "")[:80]
        if name:
            tool_lines.append(f"- {name}: {desc}")
    tools_block = "\n".join(tool_lines) if tool_lines else "(none)"

    user = f"Question: {query}\n"
    if history_summary:
        user += f"History:\n{history_summary}\n"
    user += f"Available tools:\n{tools_block}"

    payload = {
        "model": use_model,
        "messages": [
            {"role": "system", "content": PLANNER_SYSTEM},
            {"role": "user", "content": user},
        ],
        "max_tokens": PLANNER_MAX_TOKENS,
        "temperature": 0,
        "stream": False,
    }

    try:
        r = await client.post(
            f"{llm_base}/chat/completions",
            json=payload,
            headers={"Authorization": f"Bearer {llm_key}"},
            timeout=PLANNER_TIMEOUT,
        )
        r.raise_for_status()
        raw = r.json()["choices"][0]["message"]["content"] or ""
        raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.I)
        p = parse_plan_json(raw, query, user_depth)
    except Exception as e:
        log.warning("planner failed: %s", e)
        why = (
            "planner timeout"
            if "timeout" in type(e).__name__.lower() or "timeout" in str(e).lower()
            else "planner error"
        )
        p = fallback_plan(query, user_depth, why)

    ms = int((time.perf_counter() - t0) * 1000)
    return p, ms


def plan_label(intent: str) -> str:
    return {
        "search": "Searched the web",
        "compute": "Computed directly",
        "reason": "Reasoned through it",
        "answer": "Answered from memory",
    }.get(intent, "Answered")

"""Minimal stdio MCP server used by tests/test_mcp_guardrails.py.

Env:
  TOY_NAME     — server display name (default: toy)
  TOY_SLEEP    — seconds the `slow` tool sleeps (default: 0)
  TOY_FAIL     — if "1", exit immediately (failed server)
  TOY_WORDS    — if set, `blob` returns this many filler words
"""

from __future__ import annotations

import os
import sys
import time

from mcp.server.fastmcp import FastMCP

if os.environ.get("TOY_FAIL") == "1":
    sys.stderr.write("toy server refusing to start\n")
    sys.exit(1)

NAME = os.environ.get("TOY_NAME", "toy")
mcp = FastMCP(NAME)


@mcp.tool()
def ping() -> str:
    """Health check."""
    return "pong"


@mcp.tool()
def slow() -> str:
    """Sleeps TOY_SLEEP seconds then returns."""
    time.sleep(float(os.environ.get("TOY_SLEEP", "0")))
    return "done"


@mcp.tool()
def blob() -> str:
    """Returns TOY_WORDS filler words (default 50)."""
    n = int(os.environ.get("TOY_WORDS", "50"))
    # Distinct head / middle / tail markers so truncation tests can assert.
    head = ["HEAD"] + [f"h{i}" for i in range(9)]
    tail = [f"t{i}" for i in range(9)] + ["TAIL"]
    mid_n = max(0, n - len(head) - len(tail))
    mid = [f"m{i}" for i in range(mid_n)]
    return " ".join(head + mid + tail)


if __name__ == "__main__":
    mcp.run(transport="stdio")

# Boredom — what we removed

Lantern’s chrome should stay quiet. Every item below was deleted or
simplified so attention stays on the question and the answer. Kept on
purpose: the streaming caret, the sidebar streaming dot, and the
telemetry bar’s width transition (Advanced only).

| # | Removed | Why |
|---|---------|-----|
| 1 | Empty-state hero headline (“Where knowledge begins.”) | Brand flourish; input + chips are enough |
| 2 | Empty-state app-icon with orange gradient + shadow | Decorative; not needed to ask a question |
| 3 | Empty-state disclaimer footer | Restated elsewhere; crowded the first screen |
| 4 | Discover + Library nav items (“Coming soon”) | Dead ends the user cannot act on |
| 5 | Sidebar “Local user / On this device” chip | Non-actionable badge |
| 6 | Stage-line pulsing `.dot` | Stage text already says what’s happening |
| 7 | Visit-rail pulsing label dot | Same — “Visiting sites” is enough |
| 8 | Visit-host `::before` pulse on the active row | Decorative pulse while reading |
| 9 | `.src.visiting .visit-tag` pulse | Decorative; host list already updates |
| 10 | Tool-activity `slidein` entrance animation | Flourish on static content |
| 11 | Chart bar `chartIn` scale-Y animation | Decorative; chart data is the signal |
| 12 | Source-row opacity/transform transitions | Motion without new information |
| 13 | Visit-host color/border transitions | Same |
| 14 | MCP / Apps pill `mcpPulse` glow | Badge animation with no action |
| 15 | Sidebar chat skeleton pulse | Static placeholders still show “loading” |
| 16 | `@keyframes slidein` | Unused after activity entrance removal |
| 17 | Latency / Performance settings in the main list | Moved behind collapsed **Advanced** |
| 18 | “MCP” jargon in the pill and settings | Renamed to “Apps” / “connected apps” |
| 19 | “tokens”, “prefill”, “TTFT” in user-facing copy | Replaced with plain words or hidden |
| 20 | Confirm dialogs on delete chat / wipe / project | Replaced with 5s Undo (faster + safer) |
| 21 | Double-click “click again to confirm” wipe UX | Same — Undo is the safety net |
| 22 | Chatty empty-state marketing line length | Text budget; chrome stays short |
| 23 | Settings help that name-dropped Playwright MCP | Jargon; “connected apps” covers it |
| 24 | Decorative nav icons (◎ ☰ ⚙) next to labels | Labels already say Chat / Settings |
| 25 | `.soon` “Coming soon” dead-end pane path | Gone with Discover/Library |

If something still moves on a still screen, treat it as a bug.

# Motion and ambience

Lantern's first UI removed every flourish. The current UI brings back a
small, deliberate amount of motion. The rule:

**Something moves only when it means "working" or when it is the light.**

| Moves | Why |
|-------|-----|
| The amber glow behind the empty state (slow breathe) | It's the lantern — brand, not information |
| The composer's rotating border, on focus or while answering | Shows where input goes / that work is in progress |
| Pipeline step pulse + elapsed timer | Shows which stage is running |
| Streaming reasoning, the answer caret | New text is arriving |
| Source card "visiting" highlight in Deep mode | Pages are being read |

Still banned: entrance animations on static chrome, decorative badges,
"coming soon" items, confirm dialogs (Undo is faster and safer), and jargon
like "tokens" or "prefill" outside Advanced settings (tok/s on a finished
answer is the one exception).

Every animation is disabled under `prefers-reduced-motion`.

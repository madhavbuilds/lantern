# Manual QA — for someone who just opened Lantern

No setup jargon. Do these in order. Each step says what you should see.

## 1. Open the app

- You see a blank ask box and three suggestion chips. Nothing else in the middle.
- The left side lists projects and recent chats (or a short “no chats yet” note).

## 2. Ask three questions

1. Type `what is the capital of France` and press Enter.
   - Your question appears as the big heading right away.
   - You see “Thinking… Xs” with seconds counting until words arrive.
   - Sources show up as soon as search finds them, before the full answer.
   - The answer is short (about two sentences) with one or two citations.
2. In the same chat, ask `and how big is it area wise`.
   - The answer is still about France (not another country).
3. Start a **New thread**, ask `Hi`.
   - You see a muted line: “From the model's own knowledge — not verified against sources.”

## 3. Stop mid-stream

- Ask something long (or use Thorough mode).
- While words are still appearing, click the square **Stop** button (same place as Send).
- The partial answer stays. Below it: `Stopped · Continue · Retry`.
- Click **Continue** once to resume, or **Retry** to start that question over.

## 4. Switch chats while one is still answering

- Start an answer in chat A.
- Click another chat (or New thread) before A finishes.
- Chat A keeps a small pulsing orange dot in the sidebar.
- Come back to A: the text so far is there and still growing. Nothing jumps or resets.

## 5. Delete a chat, then undo

- Open a chat’s ⋯ menu → **Delete**.
- The chat disappears immediately. A bar at the bottom says “Chat deleted” with **Undo**.
- Click **Undo** within five seconds: the chat comes back.
- Delete again and wait five seconds: it stays gone after reload.

## 6. Break the model on purpose

- Quit Ollama (or stop its service).
- Ask any question.
- You should see: “Can't reach the model. Is Ollama running?” with a **Retry** button.
- No stack traces, no “Errno”, no code names.
- Start Ollama again and hit Retry: it works.

## 7. Server down

- Stop the Lantern server itself.
- A red bar appears at the top: “Can't reach Lantern's server”.
- It stays until the server is back.

## 8. Settings → Your data

- Open Settings.
- Under **Your data**, you see the file path, size, and counts.
- A note says the file is not encrypted.
- **Advanced** is collapsed; timing details live there if you want them.

/**
 * Per-chat stream registry. Switching chats changes DISPLAY, never what RUNS.
 * Cap concurrent streams at 2 — local models serve one request at a time, so
 * more than 2 in flight just means everything is slow.
 */
export const MAX_STREAMS = 2;

/** @type {Map<string, object>} */
export const streams = new Map();

/** @type {Array<object>} */
export const queue = [];

/** @type {Map<string, string>} draft text per chat */
export const drafts = new Map();

/** @type {Map<string, number>} scrollTop per chat */
export const scrollPos = new Map();

let _pendingKey = 0;

export function streamKey(chatId) {
  return chatId || `__pending_${_pendingKey}`;
}

export function activeCount() {
  let n = 0;
  for (const s of streams.values()) {
    if (s.status === "streaming") n++;
  }
  return n;
}

export function getStream(chatId) {
  if (!chatId) return null;
  return streams.get(chatId) || null;
}

export function isStreaming(chatId) {
  const s = getStream(chatId);
  return !!(s && s.status === "streaming");
}

export function anyStreaming() {
  return activeCount() > 0;
}

/**
 * @param {object} opts
 * @param {string|null} opts.chatId
 * @param {string} opts.q
 * @param {string} opts.url
 * @param {object} opts.hooks - callbacks into the page
 */
export function startStream(opts) {
  const { chatId, q, url, hooks } = opts;
  if (activeCount() >= MAX_STREAMS) {
    queue.push(opts);
    hooks.onQueued?.(opts);
    return null;
  }
  return _open(opts);
}

function _open(opts) {
  const { q, url, hooks } = opts;
  let chatId = opts.chatId;
  let key = streamKey(chatId);
  if (!chatId) {
    _pendingKey += 1;
    key = streamKey(null);
  }

  const entry = {
    key,
    chatId: chatId || null,
    es: null,
    requestId: null,
    buffer: "",
    sources: [],
    plan: null,
    thinking: "",
    thinkMs: 0,
    thinkStarted: 0,
    stage: "searching",
    activity: [],
    err: "",
    timing: null,
    status: "streaming",
    startedAt: Date.now(),
    q,
    followScroll: true,
    hooks,
  };
  streams.set(key, entry);

  const es = new EventSource(url);
  entry.es = es;

  const bump = () => hooks.onUpdate?.(entry);

  es.addEventListener("request", (e) => {
    entry.requestId = JSON.parse(e.data).id;
    bump();
  });
  es.addEventListener("chat", (e) => {
    const data = JSON.parse(e.data);
    const oldKey = key;
    chatId = data.id;
    entry.chatId = chatId;
    key = chatId;
    streams.delete(oldKey);
    streams.set(key, entry);
    hooks.onChat?.(data, entry);
    bump();
  });
  es.addEventListener("stage", (e) => {
    entry.stage = JSON.parse(e.data);
    bump();
  });
  es.addEventListener("sources", (e) => {
    entry.sources = JSON.parse(e.data);
    bump();
  });
  es.addEventListener("tool", (e) => {
    const t = JSON.parse(e.data);
    entry.activity.push(t);
    entry.stage = "using " + t.name.split("__")[0];
    bump();
  });
  es.addEventListener("tool_result", (e) => {
    const t = JSON.parse(e.data);
    for (let i = entry.activity.length - 1; i >= 0; i--) {
      if (
        entry.activity[i].name === t.name &&
        entry.activity[i].ms == null &&
        !entry.activity[i].preview
      ) {
        entry.activity[i].preview = t.preview;
        if (t.ms != null) entry.activity[i].ms = t.ms;
        break;
      }
    }
    bump();
  });
  es.addEventListener("plan", (e) => {
    entry.plan = JSON.parse(e.data);
    bump();
  });
  es.addEventListener("thinking", (e) => {
    if (!entry.thinkStarted) entry.thinkStarted = Date.now();
    entry.thinking += JSON.parse(e.data);
    entry.thinkMs = Date.now() - entry.thinkStarted;
    bump();
  });
  es.addEventListener("token", (e) => {
    entry.stage = "";
    if (entry.thinkStarted && !entry.thinkMs) {
      entry.thinkMs = Date.now() - entry.thinkStarted;
    }
    entry.buffer += JSON.parse(e.data);
    hooks.onToken?.(entry);
    bump();
  });
  es.addEventListener("stopped", async () => {
    const was = entry.status;
    entry.status = "stopped";
    es.close();
    if (was === "streaming") await hooks.onStopped?.(entry);
    _drainQueue(hooks);
    bump();
  });
  es.addEventListener("timing", async (e) => {
    entry.timing = JSON.parse(e.data);
    entry.status = "done";
    es.close();
    await hooks.onDone?.(entry);
    streams.delete(entry.chatId || key);
    _drainQueue(hooks);
    bump();
  });
  es.addEventListener("error", async (e) => {
    if (entry.status !== "streaming") return;
    // EventSource fires "error" on normal close too — only treat as failure
    // when the socket is actually gone mid-stream (readyState CLOSED) without
    // a terminal event, or when the server sent an error payload.
    const hasPayload = !!(e && e.data);
    if (!hasPayload && es.readyState !== EventSource.CLOSED) return;
    if (!hasPayload && entry.buffer) {
      // Closed after partial tokens with no terminal event — treat as stopped.
      entry.status = "stopped";
      try { es.close(); } catch { /* ignore */ }
      await hooks.onStopped?.(entry);
      _drainQueue(hooks);
      bump();
      return;
    }
    entry.stage = "";
    try {
      const parsed = hasPayload ? JSON.parse(e.data) : null;
      if (parsed && typeof parsed === "object") {
        entry.err = parsed.text || parsed.message || "Something went wrong.";
        entry.errAction = parsed.action || "retry";
        entry.errFix = parsed.fix || "";
        entry.errDetail = parsed.detail || "";
      } else {
        entry.err =
          typeof parsed === "string"
            ? parsed
            : "The connection dropped.";
        entry.errAction = "retry";
      }
    } catch {
      entry.err = "The connection dropped.";
      entry.errAction = "retry";
    }
    entry.status = "error";
    try {
      es.close();
    } catch {
      /* ignore */
    }
    await hooks.onError?.(entry);
    _drainQueue(hooks);
    bump();
  });

  hooks.onStart?.(entry);
  return entry;
}

function _drainQueue(hooks) {
  while (queue.length && activeCount() < MAX_STREAMS) {
    const next = queue.shift();
    hooks.onDequeue?.(next);
    _open(next);
  }
}

export async function stopStream(chatId, apiBase = "") {
  const entry = [...streams.values()].find(
    (s) =>
      (chatId && s.chatId === chatId) ||
      (!chatId && s.status === "streaming" && (!s.chatId || s.key.startsWith("__pending_")))
  );
  if (!entry || entry.status !== "streaming") return null;
  entry.status = "stopped";
  if (entry.requestId) {
    try {
      await fetch(`${apiBase}/api/ask/${entry.requestId}`, { method: "DELETE" });
    } catch {
      /* ignore */
    }
  }
  try {
    entry.es?.close();
  } catch {
    /* ignore */
  }
  // Local stop may miss the SSE "stopped" event — still free a slot for the queue.
  _drainQueue(entry.hooks || {});
  return entry;
}

export async function stopAndDelete(chatId, apiBase, deleteFn) {
  await stopStream(chatId, apiBase);
  streams.delete(chatId);
  await deleteFn?.(chatId);
}

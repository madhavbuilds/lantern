import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { API_BASE, askUrl, fetchMcpStatus } from "./api";
import type { Mode, Source, Timing, ToolEvent } from "./types";

const HINTS: Record<Mode, string> = {
  fast: "Snippets only — nothing is fetched.",
  deep: "Reads up to 6 pages in parallel, 3.5s ceiling.",
};

function escapeHtml(s: string) {
  return s.replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" })[c]!);
}

function renderAnswer(text: string, sources: Source[]) {
  const esc = escapeHtml(text);
  return esc.replace(/\[(\d+)\]/g, (m, n) => {
    const s = sources.find((x) => x.n === +n);
    return s
      ? `<sup><a href="${s.url}" target="_blank" rel="noopener">[${n}]</a></sup>`
      : m;
  });
}

function useTheme() {
  const [theme, setTheme] = useState<"light" | "dark">(() => {
    const saved = localStorage.getItem("lantern-theme");
    if (saved === "light" || saved === "dark") return saved;
    return matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  });

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("lantern-theme", theme);
  }, [theme]);

  return {
    theme,
    toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")),
  };
}

function verdict(t: Timing, prefill: number) {
  const ttft = (t.ttft_ms / 1000).toFixed(1);
  if (t.ttft_ms < 3000) return { kind: "good" as const, text: `First token in ${ttft}s. On target.` };
  if (prefill > t.ttft_ms * 0.5) {
    return {
      kind: "warn" as const,
      text: `First token in ${ttft}s — prefill dominated. Lower BUDGET["${t.mode}"] or check ollama ps.`,
    };
  }
  if (t.fetch_ms > 3000) {
    return {
      kind: "warn" as const,
      text: `First token in ${ttft}s — fetching dominated. Try Fast mode.`,
    };
  }
  return {
    kind: "warn" as const,
    text: `First token in ${ttft}s — likely model load. Set OLLAMA_KEEP_ALIVE=-1.`,
  };
}

export default function App() {
  const { theme, toggle } = useTheme();
  const inputRef = useRef<HTMLInputElement>(null);
  const esRef = useRef<EventSource | null>(null);

  const [q, setQ] = useState("");
  const [mode, setMode] = useState<Mode>("fast");
  const [toolsOn, setToolsOn] = useState(false);
  const [toolCount, setToolCount] = useState<number | null>(null);
  const [mcpTitle, setMcpTitle] = useState("Loading MCP…");
  const [toolsDisabled, setToolsDisabled] = useState(true);

  const [busy, setBusy] = useState(false);
  const [stage, setStage] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [answer, setAnswer] = useState("");
  const [sources, setSources] = useState<Source[]>([]);
  const [activity, setActivity] = useState<ToolEvent[]>([]);
  const [timing, setTiming] = useState<Timing | null>(null);
  const [lastQ, setLastQ] = useState("");
  const [copied, setCopied] = useState(false);
  const [hasAsked, setHasAsked] = useState(false);

  useEffect(() => {
    fetchMcpStatus()
      .then((s) => {
        const n = s.tools.length;
        setToolCount(n);
        setToolsDisabled(!n);
        const errs = Object.entries(s.errors)
          .map(([k, v]) => `${k}: ${v}`)
          .join("\n");
        setMcpTitle(n ? s.tools.join("\n") : errs || "Add servers to mcp.json and restart.");
      })
      .catch(() => {
        setToolCount(null);
        setToolsDisabled(true);
        setMcpTitle(`Can't reach API${API_BASE ? ` at ${API_BASE}` : ""}. Start the Python server.`);
      });

    return () => esRef.current?.close();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.ctrlKey && e.key.toLowerCase() === "d") {
        e.preventDefault();
        setMode((m) => (m === "fast" ? "deep" : "fast"));
      }
      if (e.ctrlKey && e.key.toLowerCase() === "t") {
        e.preventDefault();
        if (!toolsDisabled) setToolsOn((v) => !v);
      }
      if (e.key === "/" && document.activeElement !== inputRef.current) {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [toolsDisabled]);

  const hint = useMemo(
    () => HINTS[mode] + (toolsOn ? " Tools add an extra model pass per call." : ""),
    [mode, toolsOn],
  );

  function reset() {
    setAnswer("");
    setSources([]);
    setTiming(null);
    setActivity([]);
    setError("");
  }

  function ask(nextMode?: Mode, query?: string) {
    if (busy) return;
    const question = (query ?? q).trim();
    if (!question) return;
    const activeMode = nextMode ?? mode;
    if (nextMode) setMode(nextMode);

    setLastQ(question);
    setHasAsked(true);
    setBusy(true);
    reset();
    setStage("searching");

    esRef.current?.close();
    const useTools = toolsOn && !toolsDisabled ? 1 : 0;
    const es = new EventSource(askUrl(question, activeMode, useTools));
    esRef.current = es;
    let buf = "";

    es.addEventListener("stage", (e) => setStage(JSON.parse(e.data)));

    es.addEventListener("sources", (e) => setSources(JSON.parse(e.data)));

    es.addEventListener("tool", (e) => {
      const t = JSON.parse(e.data) as ToolEvent;
      setActivity((prev) => [...prev, t]);
      setStage(`using ${t.name.split("__")[0]}`);
    });

    es.addEventListener("tool_result", (e) => {
      const t = JSON.parse(e.data) as { name: string; preview: string };
      setActivity((prev) => {
        const copy = [...prev];
        for (let i = copy.length - 1; i >= 0; i--) {
          if (copy[i].name === t.name && !copy[i].preview) {
            copy[i] = { ...copy[i], preview: t.preview };
            break;
          }
        }
        return copy;
      });
    });

    es.addEventListener("token", (e) => {
      setStage(null);
      buf += JSON.parse(e.data);
      setAnswer(buf);
    });

    es.addEventListener("timing", (e) => {
      setTiming(JSON.parse(e.data));
      setBusy(false);
      setStage(null);
      es.close();
    });

    es.addEventListener("error", (e) => {
      setStage(null);
      try {
        const msg = (e as MessageEvent).data
          ? JSON.parse((e as MessageEvent).data)
          : "Stream dropped. Is the Python server running on :3000?";
        setError(typeof msg === "string" ? msg : "Stream dropped.");
      } catch {
        setError("Stream dropped. Is the Python server running on :3000?");
      }
      setBusy(false);
      es.close();
    });
  }

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    ask();
  }

  async function copyAnswer() {
    await navigator.clipboard.writeText(
      document.getElementById("answer-text")?.innerText || answer,
    );
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  }

  const prefill = timing
    ? Math.max(0, timing.ttft_ms - timing.search_ms - timing.fetch_ms - timing.select_ms)
    : 0;
  const gen = timing ? Math.max(0, timing.total_ms - timing.ttft_ms) : 0;
  const segs = timing
    ? (
        [
          ["search", timing.search_ms, "var(--seg-search)"],
          ["fetch", timing.fetch_ms, "var(--seg-fetch)"],
          ["rank", timing.select_ms, "var(--seg-rank)"],
          ["prefill", prefill, "var(--accent)"],
          ["write", gen, "var(--accent-soft)"],
        ] as const
      ).filter((s) => s[1] > 0)
    : [];
  const v = timing ? verdict(timing, prefill) : null;

  return (
    <div className="shell">
      <div className="glow" aria-hidden="true" />
      <div className="grain" aria-hidden="true" />

      <main className="wrap">
        <header className="hero">
          <div className="hero-top">
            <div className="brand">
              <span className="flame" aria-hidden="true" />
              <h1>Lantern</h1>
            </div>
            <div className="meta">
              <div className="pill" title={mcpTitle}>
                MCP ·{" "}
                <b>
                  {toolCount === null
                    ? "…"
                    : toolCount
                      ? `${toolCount} tool${toolCount > 1 ? "s" : ""}`
                      : "no tools"}
                </b>
              </div>
              <button className="pill icon" type="button" onClick={toggle} title="Toggle theme">
                {theme === "dark" ? "☀" : "◐"}
              </button>
            </div>
          </div>

          <p className="tagline">Local answers. Your model. No API keys.</p>

          <form className="ask" onSubmit={onSubmit}>
            <input
              ref={inputRef}
              type="text"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Ask anything"
              autoComplete="off"
              autoFocus
            />
            <button className="go" type="submit" disabled={busy}>
              {busy ? "…" : "Ask"}
            </button>
          </form>

          <div className="controls">
            <div className="modes" role="group" aria-label="Search depth">
              <button
                type="button"
                aria-pressed={mode === "fast"}
                onClick={() => setMode("fast")}
              >
                Fast
              </button>
              <button
                type="button"
                aria-pressed={mode === "deep"}
                onClick={() => setMode("deep")}
              >
                Deep
              </button>
            </div>

            <label className={`tools ${toolsOn ? "on" : ""}`}>
              <input
                type="checkbox"
                checked={toolsOn}
                disabled={toolsDisabled}
                onChange={(e) => setToolsOn(e.target.checked)}
              />
              <span className="track" />
              <span>Tools</span>
            </label>
          </div>

          <p className="hint">{hint}</p>
        </header>

        {stage && (
          <div className="stage">
            <span className="dot" />
            <span>{stage}</span>
          </div>
        )}

        {error && <div className="err">{error}</div>}

        {activity.length > 0 && (
          <div className="activity">
            {activity.map((t, i) => (
              <div className="act" key={`${t.name}-${i}`}>
                → <b>{t.name.replace("__", " · ")}</b> {JSON.stringify(t.args)}
                {t.preview && <span className="res">{t.preview}</span>}
              </div>
            ))}
          </div>
        )}

        {answer && (
          <div
            id="answer-text"
            className="answer"
            dangerouslySetInnerHTML={{
              __html:
                renderAnswer(answer, sources) +
                (busy ? '<span class="caret"></span>' : ""),
            }}
          />
        )}

        {timing && (
          <div className="actions">
            <button type="button" onClick={copyAnswer}>
              {copied ? "Copied" : "Copy answer"}
            </button>
            {timing.mode === "fast" && (
              <button type="button" onClick={() => ask("deep", lastQ)}>
                Ask again in Deep
              </button>
            )}
          </div>
        )}

        {sources.length > 0 && (
          <section className="sources">
            <h2>Sources</h2>
            {sources.map((s) => (
              <div className="src" key={s.n}>
                <span className="n">{s.n}</span>
                <div>
                  <a href={s.url} target="_blank" rel="noopener">
                    {s.title || s.host}
                  </a>
                  <span className="host">
                    {s.host}
                    {s.read ? (
                      <>
                        {" · "}
                        <span className="read">read in full</span>
                      </>
                    ) : null}
                  </span>
                </div>
              </div>
            ))}
          </section>
        )}

        {timing && (
          <section className="telemetry">
            <div className="bar">
              {segs.map(([name, ms, color]) => (
                <div
                  key={name}
                  style={{
                    width: `${((ms / (timing.total_ms || 1)) * 100).toFixed(2)}%`,
                    background: color,
                  }}
                />
              ))}
            </div>
            <div className="legend">
              {segs.map(([name, ms, color]) => (
                <span key={name}>
                  <i style={{ background: color }} />
                  {name} <b>{ms}ms</b>
                </span>
              ))}
              <span>
                context <b>{timing.context_tokens} tok</b>
              </span>
            </div>
            {v && <div className={`verdict ${v.kind}`}>{v.text}</div>}
          </section>
        )}

        {!hasAsked && (
          <div className="empty">
            <kbd>/</kbd> focus · <kbd>Ctrl</kbd>+<kbd>D</kbd> depth ·{" "}
            <kbd>Ctrl</kbd>+<kbd>T</kbd> tools
            <br />
            Run Docker + Ollama, then open this desktop app with{" "}
            <code>npm start</code>.
          </div>
        )}
      </main>
    </div>
  );
}

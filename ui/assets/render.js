/**
 * Minimal markdown + lantern-chart SVG renderer.
 * Escape HTML first; incomplete mid-stream syntax degrades to plain text.
 */
export function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
  );
}

/** Whitelist http(s) only — blocks javascript:/data:/vbscript: hrefs. */
export function safeHttpUrl(url) {
  try {
    const u = new URL(String(url || "").trim());
    if (u.protocol !== "http:" && u.protocol !== "https:") return "";
    return u.href;
  } catch {
    return "";
  }
}

function cite(html, sources) {
  return html.replace(/\[(\d+)\]/g, (m, n) => {
    const s = (sources || []).find((x) => x.n === +n);
    if (!s) return m;
    const href = safeHttpUrl(s.url);
    return href
      ? `<sup class="cite"><a href="${escapeHtml(href)}" target="_blank" rel="noopener">[${n}]</a></sup>`
      : `<sup class="cite">[${n}]</sup>`;
  });
}

function inlineFmt(text, sources) {
  let s = escapeHtml(text);
  // code first so we don't format inside backticks
  s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, "$1<em>$2</em>");
  return cite(s, sources);
}

function parseTable(rows, sources) {
  if (rows.length < 2) return null;
  const split = (line) =>
    line
      .replace(/^\||\|$/g, "")
      .split("|")
      .map((c) => c.trim());
  const head = split(rows[0]);
  const sep = split(rows[1]);
  if (!sep.every((c) => /^:?-+:?$/.test(c)) || sep.length !== head.length) return null;
  const body = rows.slice(2).map(split).filter((r) => r.length === head.length);
  let html = '<div class="md-table-wrap"><table class="md-table"><thead><tr>';
  for (const h of head) html += `<th>${inlineFmt(h, sources)}</th>`;
  html += "</tr></thead><tbody>";
  for (const r of body) {
    html += "<tr>";
    for (const c of r) html += `<td>${inlineFmt(c, sources)}</td>`;
    html += "</tr>";
  }
  html += "</tbody></table></div>";
  return html;
}

function chartSvg(spec) {
  const type = spec.type;
  const labels = Array.isArray(spec.labels) ? spec.labels : [];
  const series = Array.isArray(spec.series) ? spec.series : [];
  const title = spec.title || "";
  const unit = spec.unit || "";

  const nPts = Math.max(labels.length, ...series.map((s) => (s.values || []).length), 0);
  if (nPts > 12 || series.length > 3) {
    // Too big — fall back to a table note.
    let t = `<div class="chart-note">Too many points for a chart (${nPts} pts, ${series.length} series) — showing table.</div>`;
    t += '<div class="md-table-wrap"><table class="md-table"><thead><tr><th></th>';
    for (const l of labels.slice(0, 20)) t += `<th>${escapeHtml(l)}</th>`;
    t += "</tr></thead><tbody>";
    for (const s of series.slice(0, 5)) {
      t += `<tr><td>${escapeHtml(s.name || "")}</td>`;
      for (let i = 0; i < labels.length && i < 20; i++) {
        t += `<td>${escapeHtml(String((s.values || [])[i] ?? ""))}</td>`;
      }
      t += "</tr>";
    }
    t += "</tbody></table></div>";
    return t;
  }
  if (!["bar", "line", "pie"].includes(type) || !labels.length || !series.length) {
    return null;
  }

  const W = 640, H = 240, pad = { t: title ? 36 : 20, r: 16, b: 40, l: 44 };
  const iw = W - pad.l - pad.r;
  const ih = H - pad.t - pad.b;
  const colors = ["var(--accent)", "var(--fast)", "var(--tool)"];
  const reduce = typeof matchMedia === "function" && matchMedia("(prefers-reduced-motion: reduce)").matches;

  let svg = `<svg class="lantern-chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escapeHtml(title || type + " chart")}">`;
  svg += `<rect width="${W}" height="${H}" fill="var(--surface)" rx="8"/>`;
  if (title) {
    svg += `<text x="${W / 2}" y="22" text-anchor="middle" fill="var(--ink)" font-size="13" font-family="var(--ui)">${escapeHtml(title)}</text>`;
  }

  if (type === "pie") {
    const vals = series[0].values || [];
    const sum = vals.reduce((a, b) => a + (+b || 0), 0) || 1;
    const cx = W / 2, cy = pad.t + ih / 2, r = Math.min(iw, ih) / 2 - 8;
    let ang = -Math.PI / 2;
    vals.forEach((v, i) => {
      const a = ((+v || 0) / sum) * Math.PI * 2;
      const x1 = cx + r * Math.cos(ang), y1 = cy + r * Math.sin(ang);
      ang += a;
      const x2 = cx + r * Math.cos(ang), y2 = cy + r * Math.sin(ang);
      const large = a > Math.PI ? 1 : 0;
      svg += `<path d="M ${cx} ${cy} L ${x1} ${y1} A ${r} ${r} 0 ${large} 1 ${x2} ${y2} Z" fill="${colors[i % colors.length]}" opacity=".85"/>`;
    });
    labels.forEach((l, i) => {
      const x = 16, y = H - 12 - (labels.length - 1 - i) * 14;
      svg += `<rect x="${x}" y="${y - 8}" width="8" height="8" fill="${colors[i % colors.length]}"/>`;
      svg += `<text x="${x + 14}" y="${y}" fill="var(--muted)" font-size="11" font-family="var(--mono)">${escapeHtml(l)} ${vals[i] ?? ""}${unit ? " " + escapeHtml(unit) : ""}</text>`;
    });
  } else {
    const all = series.flatMap((s) => s.values || []).map(Number).filter((n) => !Number.isNaN(n));
    const max = Math.max(...all, 1);
    // axes
    svg += `<line x1="${pad.l}" y1="${pad.t}" x2="${pad.l}" y2="${pad.t + ih}" stroke="var(--line)"/>`;
    svg += `<line x1="${pad.l}" y1="${pad.t + ih}" x2="${pad.l + iw}" y2="${pad.t + ih}" stroke="var(--line)"/>`;
    svg += `<text x="8" y="${pad.t + 8}" fill="var(--muted)" font-size="10" font-family="var(--mono)">${escapeHtml(String(max))}${unit ? escapeHtml(unit) : ""}</text>`;

    if (type === "bar") {
      const groups = labels.length;
      const gw = iw / groups;
      const bw = Math.min(28, (gw * 0.7) / series.length);
      series.forEach((s, si) => {
        (s.values || []).forEach((v, i) => {
          const h = (Math.max(0, +v || 0) / max) * ih;
          const x = pad.l + i * gw + gw * 0.15 + si * (bw + 2);
          const y = pad.t + ih - h;
          const anim = reduce ? "" : ` class="chart-bar"`;
          svg += `<rect${anim} x="${x}" y="${y}" width="${bw}" height="${h}" fill="${colors[si % colors.length]}" rx="2"/>`;
          if (series.length === 1) {
            svg += `<text x="${x + bw / 2}" y="${y - 4}" text-anchor="middle" fill="var(--muted)" font-size="9" font-family="var(--mono)">${escapeHtml(String(v))}</text>`;
          }
        });
      });
    } else {
      // line
      series.forEach((s, si) => {
        const pts = (s.values || []).map((v, i) => {
          const x = pad.l + (labels.length <= 1 ? iw / 2 : (i / (labels.length - 1)) * iw);
          const y = pad.t + ih - (Math.max(0, +v || 0) / max) * ih;
          return [x, y];
        });
        if (pts.length) {
          svg += `<polyline fill="none" stroke="${colors[si % colors.length]}" stroke-width="2" points="${pts.map((p) => p.join(",")).join(" ")}"/>`;
          for (const [x, y] of pts) {
            svg += `<circle cx="${x}" cy="${y}" r="3" fill="${colors[si % colors.length]}"/>`;
          }
        }
      });
    }

    labels.forEach((l, i) => {
      const x = pad.l + (labels.length <= 1 ? iw / 2 : (i + 0.5) * (iw / labels.length));
      svg += `<text x="${x}" y="${H - 14}" text-anchor="middle" fill="var(--muted)" font-size="10" font-family="var(--mono)">${escapeHtml(String(l).slice(0, 12))}</text>`;
    });

    if (series.length > 1) {
      series.forEach((s, i) => {
        const x = pad.l + i * 110;
        svg += `<rect x="${x}" y="${H - 8}" width="8" height="8" fill="${colors[i % colors.length]}" transform="translate(0,-10)"/>`;
        svg += `<text x="${x + 12}" y="${H - 10}" fill="var(--muted)" font-size="10" font-family="var(--mono)">${escapeHtml(s.name || "series " + (i + 1))}</text>`;
      });
    }
  }
  svg += "</svg>";
  return svg;
}

function renderFence(lang, body, sources) {
  if (lang === "lantern-chart") {
    try {
      const spec = JSON.parse(body);
      const svg = chartSvg(spec);
      if (svg) return `<div class="chart-wrap">${svg}</div>`;
    } catch {
      /* fall through to code block */
    }
  }
  const id = "c" + Math.random().toString(36).slice(2, 9);
  return `<div class="md-code"><button type="button" class="copy-btn" data-copy="${id}">Copy</button><pre id="${id}"><code>${escapeHtml(body)}</code></pre></div>`;
}

/**
 * Parse markdown-ish model output into safe HTML.
 * Incomplete fences/tables mid-stream degrade to escaped plain text lines.
 */
export function renderMarkdown(text, sources) {
  const raw = text || "";
  const lines = raw.split("\n");
  const out = [];
  let i = 0;
  let para = [];

  const flushPara = () => {
    if (!para.length) return;
    const t = para.join(" ");
    out.push(`<p>${inlineFmt(t, sources)}</p>`);
    para = [];
  };

  while (i < lines.length) {
    const line = lines[i];

    // fenced code
    const fence = line.match(/^```([\w-]*)\s*$/);
    if (fence) {
      flushPara();
      const lang = fence[1] || "";
      const buf = [];
      i++;
      let closed = false;
      while (i < lines.length) {
        if (lines[i].startsWith("```")) {
          closed = true;
          i++;
          break;
        }
        buf.push(lines[i]);
        i++;
      }
      if (!closed) {
        // incomplete — plain escaped text
        out.push(`<p>${inlineFmt("```" + lang, sources)}</p>`);
        for (const l of buf) out.push(`<p>${inlineFmt(l, sources)}</p>`);
      } else {
        out.push(renderFence(lang, buf.join("\n"), sources));
      }
      continue;
    }

    // table
    if (line.trim().startsWith("|") && line.includes("|", 1)) {
      flushPara();
      const rows = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(lines[i]);
        i++;
      }
      const table = parseTable(rows, sources);
      if (table) out.push(table);
      else {
        for (const r of rows) out.push(`<p>${inlineFmt(r, sources)}</p>`);
      }
      continue;
    }

    // headings
    const h = line.match(/^(#{2,3})\s+(.+)$/);
    if (h) {
      flushPara();
      const lvl = h[1].length;
      out.push(`<h${lvl} class="md-h">${inlineFmt(h[2], sources)}</h${lvl}>`);
      i++;
      continue;
    }

    // lists
    const ul = line.match(/^[-*]\s+(.+)$/);
    const ol = line.match(/^(\d+)\.\s+(.+)$/);
    if (ul || ol) {
      flushPara();
      const ordered = !!ol;
      const items = [];
      while (i < lines.length) {
        const u = lines[i].match(/^[-*]\s+(.+)$/);
        const o = lines[i].match(/^\d+\.\s+(.+)$/);
        if (ordered ? o : u) {
          items.push(ordered ? o[1] : u[1]);
          i++;
        } else break;
      }
      const tag = ordered ? "ol" : "ul";
      out.push(
        `<${tag} class="md-list">${items.map((it) => `<li>${inlineFmt(it, sources)}</li>`).join("")}</${tag}>`
      );
      continue;
    }

    if (!line.trim()) {
      flushPara();
      i++;
      continue;
    }
    para.push(line.trim());
    i++;
  }
  flushPara();
  return out.join("") || `<p>${inlineFmt(raw, sources)}</p>`;
}

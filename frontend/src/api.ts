import type { McpStatus } from "./types";

/** Desktop app always talks to the local Python sidecar. */
export const API_BASE = "http://127.0.0.1:7451";

export async function fetchMcpStatus(): Promise<McpStatus> {
  const r = await fetch(`${API_BASE}/api/mcp`);
  if (!r.ok) throw new Error("MCP status failed");
  return r.json();
}

export function askUrl(q: string, mode: string, tools: number): string {
  const params = new URLSearchParams({
    q,
    mode,
    tools: String(tools),
  });
  return `${API_BASE}/api/ask?${params}`;
}

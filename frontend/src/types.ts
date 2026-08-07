export type Mode = "fast" | "deep";

export type Source = {
  n: number;
  title: string;
  url: string;
  host: string;
  read: boolean;
};

export type Timing = {
  search_ms: number;
  fetch_ms: number;
  select_ms: number;
  ttft_ms: number;
  total_ms: number;
  context_tokens: number;
  sources_read: number;
  mode: Mode;
};

export type ToolEvent = {
  name: string;
  args: Record<string, unknown>;
  preview?: string;
};

export type McpStatus = {
  tools: string[];
  errors: Record<string, string>;
};

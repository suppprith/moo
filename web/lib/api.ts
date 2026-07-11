// Thin client for the moo search API. Base URL is configurable so the same
// build works against a local dev API or a self-hosted instance.
import type {
  Graph,
  Mode,
  ResearchReport,
  ResearchStep,
  SearchResponse,
  SubQuestion,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE?.replace(/\/$/, "") || "http://127.0.0.1:8000";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

async function getJson<T>(path: string, signal?: AbortSignal): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { signal });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, `Cannot reach the API at ${API_BASE}. Is it running?`);
  }
  if (!res.ok) {
    throw new ApiError(res.status, `API returned ${res.status}`);
  }
  return (await res.json()) as T;
}

export function search(
  q: string,
  mode: Mode = "raw",
  opts: { k?: number; signal?: AbortSignal } = {},
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q, mode });
  if (opts.k) params.set("k", String(opts.k));
  return getJson<SearchResponse>(`/search?${params}`, opts.signal);
}

export function expandNode(node: string, signal?: AbortSignal): Promise<Graph> {
  return getJson<Graph>(`/graph/expand?node=${encodeURIComponent(node)}`, signal);
}

// ---- deep research: stream POST /research/stream (SSE) ----

export interface ResearchHandlers {
  onPlan?: (p: { intent: string; entities: string[]; sub_questions: SubQuestion[] }) => void;
  onRun?: (r: { run_id: string; status: string }) => void;
  onProgress?: (s: ResearchStep) => void;
  onReport?: (r: ResearchReport) => void;
  onDone?: (d: { run_id: string; status: string }) => void;
  onError?: (e: { code: string; message: string }) => void;
}

function dispatchFrame(frame: string, h: ResearchHandlers) {
  let event = "";
  let dataLine = "";
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLine = line.slice(5).trim();
  }
  if (!event || !dataLine) return;
  let data: unknown;
  try {
    data = JSON.parse(dataLine);
  } catch {
    return;
  }
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const d = data as any;
  switch (event) {
    case "plan": h.onPlan?.(d); break;
    case "run": h.onRun?.(d); break;
    case "progress": h.onProgress?.(d); break;
    case "report": h.onReport?.(d); break;
    case "done": h.onDone?.(d); break;
    case "error": h.onError?.(d.error ?? d); break;
  }
}

export async function deepResearch(
  question: string,
  handlers: ResearchHandlers,
  opts: { k?: number; maxSteps?: number; signal?: AbortSignal } = {},
): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/research/stream`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        question,
        k: opts.k ?? 5,
        max_steps: opts.maxSteps ?? 4,
        max_seconds: 120,
      }),
      signal: opts.signal,
    });
  } catch (e) {
    if ((e as Error).name === "AbortError") throw e;
    throw new ApiError(0, `Cannot reach the API at ${API_BASE}. Is it running?`);
  }
  if (!res.ok || !res.body) throw new ApiError(res.status, `API returned ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split("\n\n");
    buffer = frames.pop() ?? "";
    for (const frame of frames) if (frame.trim()) dispatchFrame(frame, handlers);
  }
  if (buffer.trim()) dispatchFrame(buffer, handlers);
}

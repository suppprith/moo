// Thin client for the moo search API. Base URL is configurable so the same
// build works against a local dev API or a self-hosted instance.
import type { Graph, Mode, SearchResponse } from "./types";

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

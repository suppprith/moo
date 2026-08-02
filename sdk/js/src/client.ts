/**
 * The moo client. Works anywhere `fetch` exists: Node 20+, Bun, Deno, and edge
 * runtimes.
 */

import { MooError, fromEnvelope } from './errors.ts';
import { decodeStream } from './sse.ts';
import type { StreamEvent } from './sse.ts';
import type {
  ExtractOptions,
  ExtractResponse,
  GraphOptions,
  ResearchOptions,
  ResearchReport,
  SearchOptions,
  SearchResponse,
  SearchStreamOptions,
  WebSearchOptions,
  WebSearchResponse,
} from './types.ts';

export const DEFAULT_BASE_URL = 'http://127.0.0.1:8000';
const DEFAULT_TIMEOUT_MS = 60_000;
const DEFAULT_MAX_RETRIES = 2;
const RETRY_STATUSES = new Set([408, 429, 500, 502, 503, 504]);
const MAX_BACKOFF_MS = 20_000;
const USER_AGENT = 'moo-js/0.1.0';

export type FetchLike = (input: string, init?: RequestInit) => Promise<Response>;

export interface MooOptions {
  apiKey?: string;
  baseUrl?: string;
  timeout?: number;
  maxRetries?: number;
  fetch?: FetchLike;
  /** Milliseconds to wait before retry `attempt`; defaults to jittered exponential
   *  backoff, or the server's `Retry-After` when it sent one. */
  retryDelay?: (attempt: number, retryAfterSeconds?: number) => number;
}

/**
 * OpenAPI `(method, path)` pairs to the client method that covers them. The
 * contract test fails when moo publishes an endpoint the SDK has not mapped.
 * `GET /v1/web_search` is the same operation as the POST form, which is what
 * `webSearch` uses.
 */
export const ENDPOINTS: Record<string, string> = {
  'GET /health': 'health',
  'GET /usage': 'usage',
  'GET /contract': 'contract',
  'GET /v1/tools': 'tools',
  'GET /search': 'search',
  'GET /search/stream': 'searchStream',
  'POST /v1/web_search': 'webSearch',
  'GET /v1/web_search': 'webSearch',
  'POST /v1/extract': 'extract',
  'POST /research': 'research',
  'POST /research/stream': 'researchStream',
  'GET /research/{run_id}': 'getResearch',
  'GET /source/{id}': 'source',
  'GET /chunk/{id}': 'chunk',
  'GET /claim/{id}': 'claim',
  'GET /graph': 'graph',
  'GET /graph/expand': 'expandGraph',
};

function env(name: string): string | undefined {
  const proc = (globalThis as { process?: { env?: Record<string, string | undefined> } }).process;
  return proc?.env?.[name] || undefined;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function backoffMs(attempt: number, retryAfter?: number): number {
  if (retryAfter !== undefined) return Math.min(retryAfter * 1000, MAX_BACKOFF_MS);
  return Math.min(MAX_BACKOFF_MS, 2 ** attempt * 500 * (1 + Math.random()));
}

function retryAfterSeconds(response: Response): number | undefined {
  const raw = response.headers.get('retry-after');
  if (!raw) return undefined;
  const parsed = Number(raw);
  return Number.isFinite(parsed) ? Math.max(0, Math.round(parsed)) : undefined;
}

/** Drop unset options so the server's own defaults apply. */
function query(params: Record<string, unknown>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    search.set(key, String(value));
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : '';
}

function body(params: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null) continue;
    out[key] = value;
  }
  return out;
}

interface RequestSpec {
  method: string;
  path: string;
  json?: Record<string, unknown>;
}

export class Moo {
  readonly baseUrl: string;
  readonly apiKey?: string;
  readonly timeout: number;
  readonly maxRetries: number;
  private readonly fetchImpl: FetchLike;
  private readonly retryDelay: (attempt: number, retryAfterSeconds?: number) => number;

  constructor(options: MooOptions = {}) {
    this.retryDelay = options.retryDelay ?? backoffMs;
    const base = options.baseUrl ?? env('MOO_BASE_URL') ?? DEFAULT_BASE_URL;
    this.baseUrl = base.replace(/\/+$/, '');
    this.apiKey = options.apiKey ?? env('MOO_API_KEY');
    this.timeout = options.timeout ?? DEFAULT_TIMEOUT_MS;
    this.maxRetries = Math.max(0, options.maxRetries ?? DEFAULT_MAX_RETRIES);
    this.fetchImpl = options.fetch ?? (globalThis.fetch.bind(globalThis) as FetchLike);
  }

  private headers(stream: boolean): Record<string, string> {
    const headers: Record<string, string> = {
      'User-Agent': USER_AGENT,
      Accept: stream ? 'text/event-stream' : 'application/json',
    };
    if (this.apiKey) headers.Authorization = `Bearer ${this.apiKey}`;
    return headers;
  }

  private init(spec: RequestSpec, stream: boolean): RequestInit {
    const headers = this.headers(stream);
    if (spec.json !== undefined) headers['Content-Type'] = 'application/json';
    return {
      method: spec.method,
      headers,
      body: spec.json === undefined ? undefined : JSON.stringify(spec.json),
      signal: AbortSignal.timeout(this.timeout),
    };
  }

  private async send(spec: RequestSpec, stream: boolean): Promise<Response> {
    const url = this.baseUrl + spec.path;
    for (let attempt = 0; ; attempt += 1) {
      let response: Response;
      try {
        response = await this.fetchImpl(url, this.init(spec, stream));
      } catch (cause) {
        if (attempt >= this.maxRetries) {
          throw new MooError(`could not reach moo at ${url}: ${String(cause)}`, {
            code: 'connection_error',
            retryable: true,
          });
        }
        await sleep(this.retryDelay(attempt));
        continue;
      }
      if (RETRY_STATUSES.has(response.status) && attempt < this.maxRetries) {
        await sleep(this.retryDelay(attempt, retryAfterSeconds(response)));
        continue;
      }
      return response;
    }
  }

  private async call<T>(spec: RequestSpec): Promise<T> {
    const response = await this.send(spec, false);
    const text = await response.text();
    let payload: unknown = undefined;
    if (text) {
      try {
        payload = JSON.parse(text);
      } catch {
        payload = undefined;
      }
    }
    if (!response.ok) {
      throw fromEnvelope(payload ?? text, response.status, retryAfterSeconds(response));
    }
    if (payload === undefined) {
      throw new MooError(`empty response body (HTTP ${response.status})`, {
        status: response.status,
        retryable: true,
      });
    }
    return payload as T;
  }

  private async *stream<T = unknown>(spec: RequestSpec): AsyncGenerator<StreamEvent<T>> {
    const response = await this.send(spec, true);
    if (!response.ok) {
      const text = await response.text();
      let payload: unknown = text;
      try {
        payload = JSON.parse(text);
      } catch {
        /* keep the raw body */
      }
      throw fromEnvelope(payload, response.status, retryAfterSeconds(response));
    }
    if (!response.body) {
      throw new MooError('stream response had no body', { retryable: true });
    }
    for await (const frame of decodeStream(response.body)) {
      if (frame.event === 'error') throw fromEnvelope(frame.data);
      yield frame as StreamEvent<T>;
    }
  }

  /** Liveness plus the contract version the server speaks. */
  health(): Promise<Record<string, unknown>> {
    return this.call({ method: 'GET', path: '/health' });
  }

  /** Masked per-key request counts. */
  usage(): Promise<Record<string, unknown>> {
    return this.call({ method: 'GET', path: '/usage' });
  }

  /** Contract version, handle formats, error envelope, and MCP tool schemas. */
  contract(): Promise<Record<string, unknown>> {
    return this.call({ method: 'GET', path: '/contract' });
  }

  /** OpenAI-style function-tool definitions to register with an agent. */
  tools(): Promise<{ tools: Array<Record<string, unknown>> }> {
    return this.call({ method: 'GET', path: '/v1/tools' });
  }

  /**
   * Ranked evidence for a query. `mode: 'raw'` (the server default) makes zero
   * LLM calls, `claims` adds the evidence layer, `full` adds a cited answer.
   */
  search(q: string, options: SearchOptions = {}): Promise<SearchResponse> {
    const path = `/search${query({ q, ...options })}`;
    return this.call({ method: 'GET', path });
  }

  /** Search as SSE events: `progress`, then `source` and `claim` rows, then `done`. */
  searchStream(q: string, options: SearchStreamOptions = {}): AsyncGenerator<StreamEvent> {
    return this.stream({ method: 'GET', path: `/search/stream${query({ q, ...options })}` });
  }

  /**
   * Drop-in replacement for an agent's `web_search` tool: `{title, url, snippet}`
   * rows fetched live, plus moo's extras. `shape: 'anthropic'` renders
   * `web_search_result` blocks.
   */
  webSearch(queryText: string, options: WebSearchOptions = {}): Promise<WebSearchResponse> {
    return this.call({
      method: 'POST',
      path: '/v1/web_search',
      json: body({
        query: queryText,
        max_results: options.maxResults,
        depth: options.depth,
        shape: options.shape,
        live: options.live,
      }),
    });
  }

  /**
   * Fetch URLs and return clean markdown with store handles. `depth: 'claims'`
   * also runs the evidence layer over each page.
   */
  extract(urls: string[], options: ExtractOptions = {}): Promise<ExtractResponse> {
    return this.call({
      method: 'POST',
      path: '/v1/extract',
      json: body({ urls, depth: options.depth, force: options.force }),
    });
  }

  /**
   * Full deep-research run (plan, multi-hop loop, cited report). Pass
   * `outputSchema` for a caller-shaped `structured` section with per-field
   * claim grounding.
   */
  research(question: string, options: ResearchOptions = {}): Promise<ResearchReport> {
    return this.call({
      method: 'POST',
      path: '/research',
      json: researchBody(question, options),
    });
  }

  /** Streamed research: `plan`, `run`, one `progress` per step, then `report` and `done`. */
  researchStream(question: string, options: ResearchOptions = {}): AsyncGenerator<StreamEvent> {
    return this.stream({
      method: 'POST',
      path: '/research/stream',
      json: researchBody(question, options),
    });
  }

  /** Re-fetch a run's status and report (polling, resume). */
  getResearch(runId: string): Promise<ResearchReport> {
    return this.call({ method: 'GET', path: `/research/${encodeURIComponent(runId)}` });
  }

  /** The full document behind a source (`doc_` handle or rowid). */
  source(handle: string): Promise<Record<string, unknown>> {
    return this.call({ method: 'GET', path: `/source/${encodeURIComponent(handle)}` });
  }

  /** One chunk with full text and its surrounding context (`chk_` handle). */
  chunk(handle: string): Promise<Record<string, unknown>> {
    return this.call({ method: 'GET', path: `/chunk/${encodeURIComponent(handle)}` });
  }

  /** A claim with confidence and its full evidence set (`clm_` handle). */
  claim(handle: string): Promise<Record<string, unknown>> {
    return this.call({ method: 'GET', path: `/claim/${encodeURIComponent(handle)}` });
  }

  /** The entity subgraph for a query, with claims attached to nodes and edges. */
  graph(q: string, options: GraphOptions = {}): Promise<Record<string, unknown>> {
    return this.call({ method: 'GET', path: `/graph${query({ q, ...options })}` });
  }

  /** The next ring of edges around an entity (`ent_` handle or name). */
  expandGraph(node: string, options: { limit?: number } = {}): Promise<Record<string, unknown>> {
    return this.call({ method: 'GET', path: `/graph/expand${query({ node, ...options })}` });
  }
}

function researchBody(question: string, options: ResearchOptions): Record<string, unknown> {
  return body({
    question,
    k: options.k,
    max_steps: options.maxSteps,
    max_seconds: options.maxSeconds,
    use_llm: options.useLlm,
    output_schema: options.outputSchema,
  });
}

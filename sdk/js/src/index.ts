/**
 * moo: search infrastructure for AI agents.
 *
 *     import { Moo } from 'moo-js';
 *
 *     const moo = new Moo();
 *     const { results } = await moo.webSearch('postgres connection pooling');
 *
 * Point an agent's existing `web_search` tool at `moo.webSearch`, or use
 * `moo.research` for a cited multi-hop report that flags where sources
 * disagree. Every method mirrors one HTTP endpoint; see the README.
 */

export { DEFAULT_BASE_URL, ENDPOINTS, Moo } from './client.ts';
export type { FetchLike, MooOptions } from './client.ts';
export { MooError, fromEnvelope, isRetryable } from './errors.ts';
export type { ErrorEnvelope, MooErrorCode, MooErrorOptions } from './errors.ts';
export { SSEDecoder, decodeStream } from './sse.ts';
export type { StreamEvent } from './sse.ts';
export type {
  Claim,
  DisputedPoint,
  Evidence,
  ExtractOptions,
  ExtractResponse,
  ExtractedPage,
  Finding,
  GraphOptions,
  ResearchOptions,
  ResearchReport,
  ResearchSource,
  ResultShape,
  SearchFormat,
  SearchMode,
  SearchOptions,
  SearchResponse,
  SearchStreamOptions,
  Source,
  WebSearchOptions,
  WebSearchResponse,
  WebSearchResult,
} from './types.ts';

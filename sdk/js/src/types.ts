/**
 * Response shapes.
 *
 * Every field is optional: moo's field selection (`fields=`), formats
 * (`format=agent`) and depths return subsets of the full contract, and unknown
 * keys are always allowed so a server that adds a field never breaks a pinned
 * SDK.
 */

export type SearchMode = 'raw' | 'claims' | 'full';
export type SearchFormat = 'full' | 'agent';
export type ResultShape = 'web' | 'anthropic';

export interface Evidence {
  relation?: 'supports' | 'contradicts' | 'explains';
  chunk_id?: number;
  strength?: number | null;
  document_url?: string | null;
  [key: string]: unknown;
}

export interface Claim {
  id?: number | string;
  text?: string;
  confidence?: number | null;
  disputed?: boolean;
  evidence?: Evidence[];
  valid?: Record<string, unknown>;
  superseded_by?: string | null;
  [key: string]: unknown;
}

export interface Source {
  chunk_id?: number;
  id?: string;
  document_url?: string;
  title?: string | null;
  source_type?: string;
  trust_score?: number | null;
  heading?: string | null;
  url_anchor?: string;
  score?: number;
  fetched_at?: string | null;
  suspicious?: boolean;
  highlights?: string[];
  relevance?: number;
  rank_signals?: Record<string, number>;
  [key: string]: unknown;
}

export interface SearchResponse {
  query?: string;
  mode?: SearchMode;
  intent?: string;
  answer?: string | null;
  claims?: Claim[];
  graph?: Record<string, unknown>;
  sources?: Source[];
  citations?: Array<Record<string, unknown>>;
  meta?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface WebSearchResult {
  title?: string;
  url?: string;
  snippet?: string | null;
  id?: string;
  source_type?: string;
  trust_score?: number | null;
  score?: number;
  fetched_at?: string | null;
  highlights?: string[];
  relevance?: number;
  suspicious?: boolean;
  [key: string]: unknown;
}

export interface WebSearchResponse {
  query?: string;
  results?: WebSearchResult[];
  notice?: string;
  live?: Record<string, unknown>;
  evidence?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ExtractedPage {
  url?: string;
  title?: string | null;
  markdown?: string;
  document?: string;
  chunks?: string[];
  error?: { code?: string; message?: string; retryable?: boolean };
  [key: string]: unknown;
}

export interface ExtractResponse {
  results?: ExtractedPage[];
  cost?: Record<string, unknown>;
  notice?: string;
  [key: string]: unknown;
}

export interface Finding {
  id?: string;
  text?: string;
  confidence?: number | null;
  citations?: number[];
  grounded?: boolean;
  independent_support?: number;
  [key: string]: unknown;
}

export interface DisputedPoint {
  id?: string;
  text?: string;
  supports?: number[];
  contradicts?: number[];
  [key: string]: unknown;
}

export interface ResearchSource {
  n?: number;
  id?: string;
  url?: string;
  title?: string | null;
  trust_tier?: string;
  [key: string]: unknown;
}

export interface ResearchReport {
  question?: string;
  executive_answer?: string;
  findings?: Finding[];
  disputed_points?: DisputedPoint[];
  open_questions?: string[];
  sources?: ResearchSource[];
  groundedness?: Record<string, unknown>;
  structured?: Record<string, unknown>;
  run_id?: string;
  status?: 'planning' | 'running' | 'done' | 'partial' | 'failed';
  steps?: Array<Record<string, unknown>>;
  coverage?: Array<Record<string, unknown>>;
  budget?: Record<string, unknown>;
  cost?: Record<string, unknown>;
  generator?: string;
  [key: string]: unknown;
}

export interface SearchOptions {
  mode?: SearchMode;
  k?: number;
  format?: SearchFormat;
  fields?: string;
  offset?: number;
  cursor?: string;
  live?: boolean;
  highlights?: boolean;
}

export interface SearchStreamOptions {
  mode?: SearchMode;
  k?: number;
  fields?: string;
}

export interface WebSearchOptions {
  maxResults?: number;
  depth?: SearchMode;
  shape?: ResultShape;
  live?: boolean;
}

export interface ExtractOptions {
  depth?: 'raw' | 'claims';
  force?: boolean;
}

export interface ResearchOptions {
  k?: number;
  maxSteps?: number;
  maxSeconds?: number;
  useLlm?: boolean;
  outputSchema?: Record<string, unknown>;
}

export interface GraphOptions {
  depth?: number;
  cap?: number;
}

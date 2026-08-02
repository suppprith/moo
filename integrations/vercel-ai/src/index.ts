/**
 * moo tools for the Vercel AI SDK.
 *
 *     import { Moo } from 'moo-js';
 *     import { mooTools } from '@moo/ai-sdk';
 *
 *     const result = await generateText({
 *       model: anthropic('claude-sonnet-5'),
 *       tools: mooTools(new Moo()),
 *       prompt: 'why is my postgres connection pool exhausted?',
 *     });
 *
 * The tools are plain AI SDK tool definitions (`description`, `inputSchema`,
 * `execute`), so they work with `generateText`, `streamText` and agents without
 * a wrapper. The client is typed structurally, so `moo-js` is optional: anything
 * with the same three methods works, including a stub in tests.
 */

import { z } from 'zod';

import { renderPages, renderReport, renderResults } from './render.ts';
import type { ResearchPayload, WebSearchPayload } from './render.ts';

export type { ResearchPayload, WebSearchPayload } from './render.ts';

export interface MooClientLike {
  webSearch(
    query: string,
    options?: { maxResults?: number; depth?: string; live?: boolean },
  ): Promise<WebSearchPayload>;
  extract(urls: string[], options?: { depth?: string }): Promise<WebSearchPayload>;
  research(
    question: string,
    options?: { maxSteps?: number; maxSeconds?: number },
  ): Promise<ResearchPayload>;
}

export interface MooToolsOptions {
  /** `raw` (default) makes zero LLM calls on moo's side; `claims` attaches the
   *  evidence layer; `full` also returns a cited answer. */
  depth?: 'raw' | 'claims' | 'full';
  /** Force live fetching on or off; the default follows moo's own configuration. */
  live?: boolean;
  maxResults?: number;
  maxSteps?: number;
  maxSeconds?: number;
  /** `text` (default) gives the model rendered markdown; `json` returns moo's raw
   *  payload, which costs more tokens but keeps handles, evidence and citations. */
  format?: 'text' | 'json';
}

export interface MooTool<Input> {
  description: string;
  inputSchema: z.ZodType<Input>;
  execute: (input: Input) => Promise<unknown>;
}

const searchInput = z.object({
  query: z.string().describe('the search query'),
  max_results: z.number().int().min(1).max(50).optional()
    .describe('how many results to return'),
});

const extractInput = z.object({
  urls: z.array(z.string()).min(1).max(10).describe('page urls to read'),
});

const researchInput = z.object({
  question: z.string().describe('the question to research'),
});

export const SEARCH_DESCRIPTION =
  'Search the web for software-engineering and computer-science questions ' +
  '(databases, languages, frameworks, build tooling, errors, systems). Returns ranked ' +
  'results with title, url and snippet, fetched live from docs, GitHub, release notes ' +
  'and Stack Overflow, each with a trust score. Prefer this over a general web search ' +
  'for coding questions.';

export const EXTRACT_DESCRIPTION =
  'Fetch one or more URLs and return each page as clean markdown. Use when you already ' +
  'know which pages to read. Failures are reported per URL, so a bad link does not lose ' +
  'the rest of the batch.';

export const RESEARCH_DESCRIPTION =
  'Research a software question end to end: it plans sub-questions, runs multi-hop ' +
  'retrieval over live sources, and returns an answer where every finding is cited, plus ' +
  'the points where sources disagree and what stayed unanswered. Slower than moo_search. ' +
  'Use it when the answer depends on evidence that may conflict or has changed between ' +
  'versions.';

/** Every moo tool, sharing one client. Spread the result into `tools`. */
export function mooTools(client: MooClientLike, options: MooToolsOptions = {}) {
  const asJson = options.format === 'json';

  return {
    moo_search: {
      description: SEARCH_DESCRIPTION,
      inputSchema: searchInput,
      execute: async ({ query, max_results }: z.infer<typeof searchInput>) => {
        const payload = await client.webSearch(query, {
          maxResults: max_results ?? options.maxResults,
          depth: options.depth,
          live: options.live,
        });
        return asJson ? payload : renderResults(payload);
      },
    },
    moo_extract: {
      description: EXTRACT_DESCRIPTION,
      inputSchema: extractInput,
      execute: async ({ urls }: z.infer<typeof extractInput>) => {
        const payload = await client.extract(urls, { depth: options.depth });
        return asJson ? payload : renderPages(payload);
      },
    },
    moo_deep_research: {
      description: RESEARCH_DESCRIPTION,
      inputSchema: researchInput,
      execute: async ({ question }: z.infer<typeof researchInput>) => {
        const report = await client.research(question, {
          maxSteps: options.maxSteps,
          maxSeconds: options.maxSeconds,
        });
        return asJson ? report : renderReport(report);
      },
    },
  };
}

export { renderPages, renderReport, renderResults } from './render.ts';

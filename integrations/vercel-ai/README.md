# moo-ai-sdk

[moo](https://github.com/suppprith/moo) tools for the [Vercel AI SDK](https://ai-sdk.dev): live web search scoped to software and computer science, with claim-level evidence, trust scores, and cited deep research.

```bash
npm install moo-ai-sdk moo-js
```

```ts
import { anthropic } from '@ai-sdk/anthropic';
import { generateText } from 'ai';
import { Moo } from 'moo-js';
import { mooTools } from 'moo-ai-sdk';

const { text } = await generateText({
  model: anthropic('claude-sonnet-5'),
  tools: mooTools(new Moo()),
  prompt: 'why is my postgres connection pool exhausted?',
});
```

| Tool | What the model gets |
| --- | --- |
| `moo_search` | ranked live results with title, url, snippet and a trust score, scoped to docs, GitHub, release notes and Stack Overflow |
| `moo_extract` | specific URLs as clean markdown, with per-URL failures instead of an all-or-nothing batch |
| `moo_deep_research` | a cited report: the answer, the points where sources disagree, and what stayed unanswered |

These are plain AI SDK tool definitions (`description`, `inputSchema`, `execute`), so they work with `generateText`, `streamText` and agents with no wrapper, and you can spread them alongside your own tools:

```ts
tools: { ...mooTools(moo), myTool }
```

## Options

```ts
mooTools(client, {
  depth: 'claims',   // 'raw' (default, zero LLM calls on moo's side) | 'claims' | 'full'
  live: true,        // force live fetching; default follows moo's configuration
  maxResults: 8,     // default when the model does not ask for a count
  maxSteps: 6,       // deep research budget
  maxSeconds: 60,
  format: 'json',    // 'text' (default) renders markdown; 'json' returns moo's raw payload
});
```

`format: 'json'` costs more tokens but keeps the `chk_`/`clm_`/`doc_` handles, evidence sets and citation numbers that the rendered text flattens away, which is what you want if your UI renders sources itself.

## Any client will do

The client is typed structurally, so `moo-js` is an optional peer dependency: anything with `webSearch`, `extract` and `research` works, including a stub in tests.

```ts
import type { MooClientLike } from 'moo-ai-sdk';
```

## A note on untrusted content

Retrieved pages are third-party data, not instructions. moo scans every page for prompt-injection patterns, marks matches rather than dropping them, and halves the source's trust. Rendered output says so inline next to the flagged result, and the raw payload carries `suspicious`.

## Development

```bash
npm test        # node --test, no test framework to install
npm run build   # tsc to dist/
```

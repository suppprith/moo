# moo-js

Official JavaScript/TypeScript client for [moo](https://github.com/suppprith/moo), search infrastructure for AI agents: live web search over software sources, an evidence layer that flags where sources disagree, and cited deep research.

```bash
npm install moo-js
```

Runs anywhere `fetch` exists: Node 20+, Bun, Deno, and edge runtimes.

## Replace your agent's web_search

```ts
import { Moo } from 'moo-js';

const moo = new Moo(); // MOO_BASE_URL / MOO_API_KEY from the environment

export async function webSearch(query: string) {
  const { results } = await moo.webSearch(query, { maxResults: 8 });
  return results;
}
```

Rows are the universal `{title, url, snippet}` shape, so existing tool wiring keeps working. moo adds `id` (a `chk_` handle you can drill into), `source_type`, `trust_score`, `relevance`, `highlights`, and `fetched_at` alongside.

Register the tool definition moo publishes instead of writing your own:

```ts
const { tools } = await moo.tools(); // OpenAI-style function tools: web_search, extract
```

## Deep research with citations

```ts
const report = await moo.research('is pgbouncer still recommended over built-in pooling', {
  maxSteps: 4,
});

console.log(report.executive_answer);
for (const point of report.disputed_points ?? []) console.log('disputed:', point.text);
```

`report.findings` carries per-claim confidence and citation numbers into `report.sources`; `report.groundedness` says how many findings are actually backed by a source that supports them. Pass `outputSchema` (a JSON schema) for a caller-shaped `structured` section where every populated field traces back to claim handles.

Stream it instead of waiting:

```ts
for await (const { event, data } of moo.researchStream('why is my connection pool exhausted')) {
  if (event === 'progress') console.log(data);
  if (event === 'report') console.log(data);
}
```

## Everything else

| Method | Endpoint | What it gives you |
| --- | --- | --- |
| `search(q, opts)` | `GET /search` | ranked evidence; `raw` makes zero LLM calls, `claims` adds the evidence layer, `full` adds a cited answer |
| `searchStream(q, opts)` | `GET /search/stream` | the same, as SSE events |
| `webSearch(query, opts)` | `POST /v1/web_search` | drop-in web-search rows |
| `extract(urls, opts)` | `POST /v1/extract` | URLs to clean markdown; `depth: 'claims'` runs evidence per page |
| `research(question, opts)` | `POST /research` | cited multi-hop report |
| `researchStream(question, opts)` | `POST /research/stream` | the same, streamed |
| `getResearch(runId)` | `GET /research/{id}` | poll or resume a run |
| `source/chunk/claim(handle)` | `GET /source\|/chunk\|/claim/{id}` | drill into any handle a result gave you |
| `graph(q)` / `expandGraph(node)` | `GET /graph`, `/graph/expand` | the entity subgraph behind a query |
| `contract()` / `tools()` / `health()` / `usage()` | | introspection |

## Configuration

```ts
const moo = new Moo({
  baseUrl: 'https://api.example.com', // or MOO_BASE_URL, default http://127.0.0.1:8000
  apiKey: 'sk-...',                   // or MOO_API_KEY, unset for a self-hosted moo
  timeout: 60_000,
  maxRetries: 2,
  fetch: customFetch,                 // any fetch-compatible function
});
```

## Errors

Every failure is a `MooError` carrying moo's structured envelope:

```ts
import { MooError } from 'moo-js';

try {
  await moo.search('...');
} catch (error) {
  if (error instanceof MooError) {
    console.error(error.code, error.requestId, error.retryable, error.retryAfter);
  }
}
```

429 and 5xx responses are retried automatically (honoring `Retry-After`) before they reach you; `invalid_request`, `not_found`, and `unauthorized` are thrown immediately. Streams commit HTTP 200 before they can fail, so a mid-stream failure arrives as an `error` event and is thrown at the point you iterate to it.

## Development

```bash
npm test        # node --test, no test framework to install
npm run build   # tsc to dist/
```

`test/contract.test.ts` reads `api/openapi.json` and fails when moo publishes an endpoint the SDK has not mapped, so drift shows up as a red test rather than a missing method.

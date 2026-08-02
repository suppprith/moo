# moo

Search infrastructure for AI agents.

An agent asks a question. moo goes out to the live web, reads the pages it finds,
pulls out the claims those pages actually make, checks the claims against each
other, and hands back an answer where every sentence points at a source. When two
sources disagree, it says so instead of quietly picking a side. When a page has
been superseded by a newer version of the thing it describes, it says that too.

It runs on your own hardware: one SQLite file, local embeddings, no query logging,
no per-query bill, and no API key needed to boot.

## Setup

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/suppprith/moo.git
cd moo/api
uv sync
```

That is the whole install. The database is created and migrated on first run, so
there is no init step to forget.

Two optional pieces make moo better. Neither is required to run it.

**A discovery provider** switches on live retrieval. Without one, moo answers out
of whatever it has already fetched and cached.

```bash
# self-hosted SearXNG, no key (enable `json` under search.formats in settings.yml)
docker run -d -p 8888:8080 searxng/searxng
export MOO_SEARXNG_URL=http://localhost:8888

# or the Brave Search API, which has a free tier
export MOO_BRAVE_API_KEY=BSA...
```

**An LLM key** sharpens query expansion, claim extraction, evidence linking and
the written answer. Bring whichever you already have: Gemini, OpenAI, Anthropic,
Ollama, or any OpenAI-compatible server. Copy `api/.env.example` to `api/.env` and
fill in one block. With no key at all, every LLM stage falls back to a
deterministic heuristic, so the pipeline still runs end to end; it just reasons
less well.

Check that it works:

```bash
uv run pytest
uv run moo "how does sqlite wal mode work"
```

## Connect an agent (MCP)

```bash
uv run python -m app.mcp_server          # stdio, zero install
uv tool install .                        # or install it, then: moo-mcp [--http]
```

**Claude Code**

```bash
claude mcp add moo -- uv run --directory "/abs/path/to/moo/api" python -m app.mcp_server
```

**Claude Desktop**, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "moo": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/moo/api", "python", "-m", "app.mcp_server"]
    }
  }
}
```

The first `search` warms the embedding model, which takes a few seconds. After
that, the agent has eight tools:

| Tool | What it does |
| --- | --- |
| `search` | Ranked sources for a query. `mode=raw` is fast and makes zero LLM calls, `claims` adds the evidence layer, `full` adds a cited answer. |
| `extract` | Hand it URLs, get clean markdown back, optionally with claims run over each page. |
| `fetch_source` | Turn a citation handle into the thing it cites: chunk text with context, the whole document, or a claim with its evidence. |
| `get_claim` | One claim: confidence, disputed flag, full evidence set with contradictions listed first. |
| `list_contradictions` | Only the claims where sources disagree. |
| `expand_graph` | The entity neighbourhood around a tool or concept, with typed relations. |
| `deep_research` | Hand off a whole question. Plans it, runs multi-hop retrieval, returns a cited report, streams progress. |
| `research_status` | Poll or re-read a research run by id. |

The tools that can return a lot (`search`, `extract`, `fetch_source`,
`deep_research`, `research_status`) take a `max_tokens` budget and shape the
payload to fit, with an explicit truncation marker instead of a silent cut.
Contradictions are never the thing that gets dropped.

Full tool reference and the workflow an agent follows: [docs/agents.md](docs/agents.md).

## Drop in as `web_search`

For agents that do not speak MCP, moo serves the universal web-search shape
(`{title, url, snippet}`), so existing `web_search` wiring can point at it
unchanged.

```bash
uv run fastapi dev app/main.py
curl -s localhost:8000/v1/tools           # OpenAI function-tool definitions
curl -s -X POST localhost:8000/v1/web_search \
     -H 'content-type: application/json' -d '{"query":"why is my Postgres query slow"}'
```

Register the definition named `web_search` from `/v1/tools` and execute calls
against `POST /v1/web_search`. Pass `"shape":"anthropic"` for `web_search_result`
blocks. Results always carry highlighted spans and a relevance score that is
comparable across queries; `depth=claims` attaches the evidence layer as an
optional field.

## Or use an SDK

```bash
pip install moo        # Python, sync + async
npm install moo-js     # JavaScript and TypeScript, Node + edge runtimes
```

```python
from moo import Moo

moo = Moo()  # MOO_BASE_URL / MOO_API_KEY from the environment

def web_search(query: str) -> list[dict]:
    return moo.web_search(query, max_results=8)["results"]
```

```ts
import { Moo } from 'moo-js';

const moo = new Moo();
const { results } = await moo.webSearch('postgres connection pooling');
```

Both cover the whole surface (search, web search, extract, deep research with SSE
streaming, handle drill-down, graph), retry `429` and `5xx` honoring `Retry-After`,
and raise a typed error carrying moo's `code`, `retryable` flag and request id. A
contract test in each SDK reads `api/openapi.json` and fails when moo grows an
endpoint the client has not caught up with.

[`sdk/python`](sdk/python) · [`sdk/js`](sdk/js)

## Or plug into your framework

```python
from langchain_moo import MooRetriever, moo_toolkit      # LangChain
from llama_index_moo import MooRetriever, MooToolSpec    # LlamaIndex
```

```ts
import { mooTools } from '@moo/ai-sdk';                  // Vercel AI SDK
```

Each package wraps the SDK in that framework's own shapes: a retriever whose
documents keep moo's handles, trust scores and injection flags, and tools for
search, page extraction and deep research. CrewAI and the OpenAI Agents SDK get
copy-paste recipes. Full guide, including what the model sees for each tool:
[docs/integrations.md](docs/integrations.md).

## Deep research

`deep_research(question)` over MCP, or `POST /research` and `POST /research/stream`
over HTTP, returns `{executive_answer, findings[], disputed_points[],
open_questions[], sources[], groundedness}`. It splits the question into
sub-questions, iterates retrieval to fill gaps and chase contradictions under a
hard step and wall-clock budget, and drops any finding it cannot cite. Runs are
persisted, so a long one can be polled or resumed by `run_id`.

Pass an `output_schema` and you also get a `structured` section shaped to your
schema, where `structured.grounding` traces every populated field back to the
claims behind it. Fields that cannot be traced come back `null` and are listed in
`ungrounded_fields`, never invented.

Reproducible demo: `uv run python -m app.eval.demo`
([recorded transcript](docs/agent-demo.md)).

## Give it URLs instead of a query

```bash
curl -s -X POST localhost:8000/v1/extract \
     -H 'content-type: application/json' \
     -d '{"urls":["https://www.sqlite.org/wal.html"],"depth":"raw"}'
```

Clean markdown per page, plus `doc_` and `chk_` handles, because the page is
stored and indexed on the way through. `depth=claims` runs the evidence layer over
each page. Failures are reported per URL, and pages still inside their cache TTL
are served without touching the network.

## From your terminal

```bash
moo "why do my containers randomly exit"        # cited answer as markdown
moo "postgres 16 parallel vacuum" --mode raw    # ranked sources, fast
moo "redis persistence" --json | jq '.claims'   # pipe the full response
moo "sqlite wal mode" --open 1                  # open the top source
```

Install with `uv tool install ./api` or `pipx install ./api`, or skip installing
and run `uv run moo "..."` from `api/`. It runs the engine in-process by default,
so no server is involved; point it at a running instance with `--url` or `MOO_URL`.
Output is pipe-friendly, with no ANSI codes when stdout is not a TTY.

## What happens to a query

```
query  -> operators + routing -> live discovery -> fetch -> clean -> chunk -> embed
       -> index: FTS5 (BM25) + sqlite-vec            [one SQLite file]
       -> hybrid retrieval (RRF) -> fusion -> rerank -> highlights
       -> claims -> evidence edges -> confidence -> versions + graph
       -> cited answer
agents <- MCP tools · /v1/web_search · /v1/extract · /search · /research
```

A few links in that chain are worth calling out.

**Typed operators** are parsed before anything else runs: `type:docs,so`,
`site:host.com`, `since:2024`, `"exact phrase"`, `-exclude`. Anything unrecognised
stays as plain search text and is reported back rather than throwing.

**Routing** looks at the query first. Literal identifiers and error strings lean
the fusion toward BM25, conceptual prose leans it toward embeddings, and a pasted
paragraph skips query fan-out entirely.

**Live fetching** ranks candidate URLs with a source policy that puts official
docs, repos, release notes and Q&A ahead of unknown sites, fetches concurrently
across hosts while staying serial within a host, and stops at a wall-clock
deadline with partial results rather than blocking on stragglers. Fetched pages go
through the same store the rest of moo searches, so the store doubles as the
cache: anything inside its tier's TTL costs no network at all, and eviction never
touches a document whose chunks back a claim.

**Ranking** is not relevance alone. Trust, corroboration across independent copies,
and recency multiply into the score, and every component is reported on the result
so you can see why something landed where it did. Reranking is a swappable last
step: a listwise LLM call, a local cross-encoder that needs no key, or off.

**Versions** are read out of the query. Ask about Redis 7 and sources speaking
about Redis 7 get boosted, while older-major-only ones get demoted and flagged.

**The evidence layer** extracts claims from the retrieved text, links each one to
supporting, contradicting and explaining chunks, then scores confidence from the
balance of evidence, counting near-duplicate copies of one source as a single
voice. A claim with real support on both sides is marked disputed rather than
averaged into false confidence. Trust follows a documented rubric: official docs
over maintainer comments over blogs over forum posts, adjusted for author role and
decayed by age at a rate that depends on the source type.

**Time** is modelled explicitly. Claims carry validity windows read from phrases
like "deprecated in 3.9", and a newer-version claim about the same subject
supersedes the older one instead of fighting it, so historical change stops
looking like live controversy.

**Retrieved pages are untrusted input.** Every page is scanned for prompt-injection
patterns. Matches are marked rather than dropped, the flag travels with the source
all the way to the caller, and the source loses half its trust. Every web-search
response carries a notice that page content is data, not instructions.

## Running it as a service

```bash
docker compose up --build     # the image bakes the model and warms it on boot
curl localhost:8000/health
```

The store lives on a mounted volume (`MOO_DB_PATH`), so the container itself is
stateless and replaceable. `api/fly.toml` is a working deploy: one machine, one
volume, HTTPS, suspend when idle. Any host with a volume and ~2 GB of memory
works the same way.

Open by default. To gate it, either set fixed keys, or issue them:

```bash
MOO_API_KEYS=key1,key2 MOO_RATE_LIMIT_PER_MIN=60 uv run fastapi dev app/main.py
uv run python -m app.keys create --label alice --quota 1000
```

Issued keys are stored as a hash, shown once, revocable, and carry their own
rate limit, quota and counters that survive a restart. Clients pass
`Authorization: Bearer <key>` or `X-API-Key`. `/health`, `/contract` and
`/v1/tools` stay open. Over-limit requests get a `429` with `Retry-After`, an
exhausted quota gets a non-retryable `429`, and `GET /usage` reports per-key
counts. No query content is ever logged.

Deploy steps, the SQLite-per-tenant storage decision and the operational notes:
[docs/hosting.md](docs/hosting.md).

Errors come back in one envelope with a stable `code`, a `retryable` flag, and a
request id echoed in the `X-Request-ID` header, so an agent can branch on the
failure instead of parsing prose. `/search/stream` and `/research/stream` are SSE.
`/contract` is the machine-readable descriptor: contract version, handle formats,
error taxonomy, and the MCP tool schemas. The OpenAPI spec is committed at
[`api/openapi.json`](api/openapi.json), and a test fails if it drifts.

## Evals

```bash
uv run python -m app.eval.benchmark        # coverage, citation accuracy, source
                                           # and contradiction recall, token cost
uv run python -m app.eval.flywheel         # score every configured engine, log history
uv run python -m app.eval.flywheel --gate  # exit 1 if moo regressed against itself
uv run python -m app.eval.stale_traps      # the one claim that has to hold
```

The benchmark compares single-shot search against `deep_research` on a versioned
task set, with a recorded baseline in
[`api/app/eval/baseline.json`](api/app/eval/baseline.json). The flywheel is the
standing version: it scores moo on every run, adds Exa and Tavily as columns when
their keys are set, and the gate fails a commit that drops moo below its own last
numbers.

**The stale-answer traps** are the one that matters. Each is a real query whose
popular answer is out of date (`datetime.utcnow()`, `ReactDOM.render`,
`PodSecurityPolicy`, `wal_level = archive`, `listen 443 ssl http2`), paired with
the answer that is actually current and the release that changed it. An engine
passes a trap only by surfacing the current answer *and* marking the old one as
outdated, whether structurally (a disputed point, a superseded claim, a
version-outdated source) or in plain text. Repeating the stale answer with no
warning is the failure being counted, and it is what a plain web search does.
`--gate` exits non-zero unless moo clears 70% and leads every competitor that ran
by a wide margin; `--transcript` writes the per-trap record, quotes included.

Right now it scores 0% here, and the saved run says why:
[`stale_trap_baseline.json`](api/app/eval/stale_trap_baseline.json) records
`live_provider: null`, so there was nothing to retrieve. The traps span Python,
React, Kubernetes and nginx while the local store holds database and framework
docs. The gate becomes meaningful the moment a discovery provider is configured,
which is the point of running it early.

## Layout

| Path | Stack | Purpose |
| --- | --- | --- |
| `api/` | Python, FastAPI, uv, SQLite | Retrieval, evidence, research engine, MCP server, HTTP API, CLI |
| `web/` | Next.js, TypeScript | Optional human search UI |
| `sdk/` | Python, TypeScript | Official clients (`sdk/python`, `sdk/js`) |
| `integrations/` | Python, TypeScript | LangChain, LlamaIndex, Vercel AI SDK packages |
| `docs/` | Markdown | Agent guide, data model, domain scope |

Day-to-day work happens in `api/`:

```bash
uv run fastapi dev app/main.py   # http://127.0.0.1:8000
uv run python -m app.db          # apply migrations by hand
uv run pytest
uv run ruff check .
```

Data model: [docs/data-model.md](docs/data-model.md). Agent guide:
[docs/agents.md](docs/agents.md).

## License

MIT, see [LICENSE](LICENSE).

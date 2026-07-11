# moo

A CS/coding-specialized search backend for AI agents — the tool a coding agent
routes its `web_search` to for software-engineering questions. Instead of ranked
links or one unverifiable paragraph, moo returns **claims** backed by typed
**evidence** (supports / contradicts / explains), each source scored for
**trust** and each claim for **confidence**, with stable handles to drill from a
citation down to the exact source.

It runs on your own hardware: a single SQLite file, local embeddings, no query
logging, no per-query cost, and no API key required to run (the LLM stages fall
back to deterministic heuristics without one).

## Why moo

| Capability | Exa / Tavily | Perplexity / built-in web_search | moo |
| --- | --- | --- | --- |
| Returns ranked snippets/content | ✅ | ✅ | ✅ |
| Synthesized answer + citations | partial | ✅ | ✅ (`full` mode) |
| Claims with per-claim confidence | ❌ | ❌ | ✅ |
| Surfaces contradictions instead of averaging them | ❌ | ❌ | ✅ (`list_contradictions`, disputed flags) |
| Typed evidence edges (supports/contradicts/explains) | ❌ | ❌ | ✅ |
| Per-source trust rubric (maintainer > forum, recency decay) | ❌ | ❌ | ✅ |
| Stable drill-down handles (citation → span → doc) | contents API | ❌ | ✅ (`fetch_source`) |
| CS "dark knowledge" (GitHub issues/PRs/release notes, author role) | generalist | generalist | ✅ specialist |
| Self-hosted, private, keyless, zero per-query cost | ❌ paid API | ❌ paid API | ✅ |

moo is deliberately not a general web search: it indexes GitHub issues/PRs,
release notes, official docs, engineering blogs, and Stack Overflow — the sources
that hold the real answer to a coding problem but that general search ranks
poorly. Coverage and freshness are traded for a specialist, verifiable evidence
layer.

## Two ways in

1. **Primitive tools** — the agent drives its own loop: `search`, `fetch_source`,
   `get_claim`, `list_contradictions`, `expand_graph`.
2. **Deep research** — hand off a whole question: `deep_research` runs
   plan → iterative multi-hop retrieval → a cited, confidence-scored report;
   `research_status` polls/resumes it.

Both are exposed over **MCP**; `search` is also available as a drop-in
**`web_search`** HTTP adapter. Full tool reference and workflow:
[docs/agents.md](docs/agents.md).

## Connect an agent (MCP)

```bash
cd api && uv sync
uv run python -m app.mcp_server         # stdio server (no install needed)
# or install it: uv tool install .  ->  moo-mcp [--http]
```

**Claude Code** — `claude mcp add moo -- uv run --directory "/abs/path/to/moo/api" python -m app.mcp_server`

**Claude Desktop** — in `claude_desktop_config.json`:

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

Runs keyless locally. First `search` warms the embedding model (a few seconds);
after that a typical flow is `search` → `fetch_source(chk_…)` for the evidence,
or `deep_research` for a full report.

## Drop-in web_search

For agents that don't speak MCP, moo serves the universal web-search shape
(`{title, url, snippet}`):

```bash
uv run fastapi dev app/main.py
curl -s localhost:8000/v1/tools           # OpenAI web_search function-tool definition
curl -s -X POST localhost:8000/v1/web_search \
     -H 'content-type: application/json' -d '{"query":"why is my Postgres query slow"}'
```

Register the definition from `/v1/tools` (named `web_search`) and execute calls
against `POST /v1/web_search`. Pass `"shape":"anthropic"` for `web_search_result`
blocks. `depth=claims` attaches the evidence layer as an optional field.

## Deep research

`deep_research(question)` (MCP) or `POST /research` / `POST /research/stream`
(HTTP) returns `{executive_answer, findings[] (confidence + citations),
disputed_points[], open_questions[], sources[], groundedness}`. It decomposes the
question, iterates retrieval to fill gaps and chase contradictions under a
budget, and every finding traces to a source — uncited claims never ship.

Reproducible demo: `uv run python -m app.eval.demo` ([recorded transcript](docs/agent-demo.md)).

## Architecture

```
connectors (GitHub · docs · blogs · SO/HN/Reddit)
        → fetch → clean → chunk
        → index: FTS5 (BM25) + sqlite-vec (embeddings)   [one SQLite file]
query   → understanding → expansion → hybrid retrieval (RRF)
        → claim extraction → evidence linking → confidence + source trust
        → knowledge graph → rerank → cited synthesis
agents  ← MCP tools · /v1/web_search · /search · /research
```

Semantic search is treated as infrastructure; the evidence + reasoning layer on
top is the product.

## Layout

| Path | Stack | Purpose |
| --- | --- | --- |
| `api/` | Python · FastAPI · uv · SQLite | Ingestion, retrieval, evidence, research engine, MCP server, HTTP API |
| `web/` | Next.js · TypeScript | Optional human search UI |
| `docs/` | Markdown | Data model, agent guide, domain scope |

## Develop

```bash
cd api
uv sync
uv run python -m app.db          # apply migrations
uv run fastapi dev app/main.py   # http://127.0.0.1:8000
uv run pytest
uv run ruff check .
```

The LLM stages (query expansion, claim/evidence extraction, synthesis) are
**bring-your-own-key**: Gemini, OpenAI, Anthropic, or any OpenAI-compatible /
local server (Ollama, vLLM, LM Studio). Configure via `MOO_LLM_PROVIDER` /
`MOO_LLM_API_KEY` / `MOO_LLM_MODEL` / `MOO_LLM_BASE_URL` in `api/.env` (gitignored;
see `api/.env.example`). Without any key the stages run deterministic heuristics —
the pipeline works end to end, quality is just better with a model. Run a local
model and nothing leaves the machine.

## Serving

Runs fully open locally. To gate it when exposed, set API keys and a per-key
rate limit:

```bash
MOO_API_KEYS=key1,key2 MOO_RATE_LIMIT_PER_MIN=60 uv run fastapi dev app/main.py
```

Clients pass `Authorization: Bearer <key>` or `X-API-Key`. `/health` and
`/v1/tools` stay open; over-limit requests get a `429` with `Retry-After`.
`GET /usage` reports masked per-key request counts. No query content is logged.

## Benchmarks

`uv run python -m app.eval.benchmark` scores the engine on a versioned task set
(coverage, citation accuracy, source recall, contradiction recall, token cost)
and compares single-shot search vs. `deep_research`. Baseline in
[`api/app/eval/baseline.json`](api/app/eval/baseline.json).

Data model: [docs/data-model.md](docs/data-model.md). Domain scope and gold
queries: [docs/v1-vertical.md](docs/v1-vertical.md).

## License

MIT — see [LICENSE](LICENSE).

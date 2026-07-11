# moo search 🐮

An **evidence-graph search engine** for developers. Instead of ten blue links or one
confident AI paragraph, moo answers a query with **claims** backed by typed **evidence**
(supports / contradicts / explains), each source scored for **trust**, each claim scored for
**confidence** — explorable as an interactive graph.

Design principles: **AI is opt-in, never automatic** (the default result page is pure
retrieval, zero LLM calls); CLI-style *interaction* (command palette, keyboard-first) with
clean modern visuals; simpler than DuckDuckGo; self-hostable and private (no query logging).

## Monorepo layout

| Path   | Stack                                   | Purpose                                  |
| ------ | --------------------------------------- | ---------------------------------------- |
| `api/` | Python · FastAPI · uv · SQLite          | Ingestion, retrieval, evidence, `/search` |
| `web/` | Next.js · TypeScript (App Router)       | Search UI, claims/evidence, graph explorer |
| `docs/`| Markdown                                | Domain scope, gold queries, decisions    |

## Architecture

```
                    ┌──────────────────────────────────────────────┐
   connectors       │  Ingestion                                   │
   GitHub · docs ──▶│  fetch → clean → chunk                       │
   blogs · SO/HN    └───────────────┬──────────────────────────────┘
                                    ▼
                    ┌──────────────────────────────────────────────┐
                    │  Indexing (single SQLite file)               │
                    │  FTS5 (BM25)  +  sqlite-vec (embeddings)     │
                    └───────────────┬──────────────────────────────┘
                                    ▼
   query ──▶ understanding ──▶ expansion ──▶ hybrid retrieval (RRF)
                                    │
                                    ▼
              claim extraction ──▶ evidence linking ──▶ confidence
              (supports / contradicts / explains)      + source trust
                                    │
                                    ▼
   ┌──────────────────────────────────────────────────────────────┐
   │  /search  →  { answer, claims[], graph, sources[] }           │
   │  mode = raw (default, no LLM) | claims | full                 │
   └───────────────┬──────────────────────────────────────────────┘
                   ▼
   web/ (search page · claims view · evidence graph)   +   moo CLI
```

## Quickstart

```bash
# API
cd api && uv sync && uv run fastapi dev app/main.py   # http://127.0.0.1:8000/health

# Web
cd web && npm install && npm run dev                  # http://localhost:3000
```

## Connect an agent (MCP) in 2 minutes

moo doubles as a **CS/coding search backend for AI agents** — the tool a coding
agent routes its `web_search` to for software-engineering questions. It ships an
MCP server exposing `search`, `fetch_source`, `get_claim`, `list_contradictions`,
and `expand_graph`, all returning compact, grounded results with opaque handles
you can drill into.

```bash
cd api && uv sync                       # once
uv run python -m app.mcp_server         # stdio server (no install needed)
# or, if installed as a tool (uv tool install . / pipx install .):
moo-mcp                                 # stdio   ·   moo-mcp --http   (streamable HTTP on :8000/mcp)
```

Point a client at it (replace the path with your absolute checkout path):

**Claude Code** — `claude mcp add moo -- uv run --directory "/abs/path/to/moo search/api" python -m app.mcp_server`

**Claude Desktop** — add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "moo": {
      "command": "uv",
      "args": ["run", "--directory", "/abs/path/to/moo search/api", "python", "-m", "app.mcp_server"]
    }
  }
}
```

**Any MCP client (installed)** — `{ "mcpServers": { "moo": { "command": "moo-mcp" } } }`,
or run `moo-mcp --http` and connect to `http://127.0.0.1:8000/mcp`.

Runs **keyless** locally (no API key, no query logging; per-key auth arrives with
serving). The first `search` warms the embedding model (~a few seconds), then a
typical agent flow is: `search` → read `sources` → `fetch_source(chk_…)` for the
full evidence, or `list_contradictions` to see where sources disagree.

## Data model

Single SQLite file; stdlib `sqlite3`, no ORM. Full detail in
[`docs/data-model.md`](docs/data-model.md); schema in
[`api/migrations/0001_init.sql`](api/migrations/0001_init.sql).

```
 document 1───∞ chunk ∞───∞ claim ∞───∞ entity
    │            │   └────────┐  │  ┌──────┘ │
    │            │  (claim_chunk)│(claim_entity)
    │            │            │  │           │
    │            └──∞ evidence ∞─┘           │
    │              (supports/contradicts/    │
    │               explains, strength)      │
    └──────── relation (entity↔entity) ──────┘

 FTS5 + sqlite-vec indexes over `chunk` are added in Phase 2.
```

The v1 domain scope, seed sources, and 15 gold queries live in
[`docs/v1-vertical.md`](docs/v1-vertical.md).

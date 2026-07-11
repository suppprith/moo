# moo-api

FastAPI backend for **moo search**, managed with [uv](https://docs.astral.sh/uv/).

## Develop

```bash
uv sync                       # install deps into .venv
uv run fastapi dev app/main.py   # dev server with reload on http://127.0.0.1:8000
uv run ruff check .           # lint
uv run ruff format .          # format
```

- `GET /health` → `{"status": "ok"}` liveness probe.

## MCP server (`moo-mcp`)

Expose moo's search to AI agents over MCP:

```bash
uv run python -m app.mcp_server        # stdio (default)
uv run python -m app.mcp_server --http # streamable HTTP on http://127.0.0.1:8000/mcp
# installed console script (uv tool install . / pipx install .):
moo-mcp [--http] [--host H] [--port P]
```

Tools: `search`, `fetch_source`, `get_claim`, `list_contradictions`, `expand_graph`,
`deep_research`, `research_status`.
Client config + the 2-minute quickstart are in the [root README](../README.md#connect-an-agent-mcp-in-2-minutes).

## Benchmarks

Deep-research eval over a versioned task set (coverage, citation accuracy, source
recall, contradiction recall, token cost) — single-shot search vs. `deep_research`:

```bash
uv run python -m app.eval.benchmark [--max-steps N] [--no-llm] [--save baseline.json]
```

A recorded baseline lives in [`app/eval/baseline.json`](app/eval/baseline.json);
every engine change should show a before/after. (Numbers are heuristic-gated until
a `GEMINI_API_KEY` is set — contradiction recall in particular needs the LLM.)

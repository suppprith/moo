# moo (Python SDK)

Official Python client for [moo](https://github.com/suppprith/moo), search infrastructure for AI agents: live web search over software sources, an evidence layer that flags where sources disagree, and cited deep research.

```bash
pip install moo-search
```

The distribution is `moo-search` because `moo` is taken on PyPI; the import is still `moo`.

## Replace your agent's web_search

```python
from moo import Moo

client = Moo()  # MOO_BASE_URL / MOO_API_KEY from the environment

def web_search(query: str) -> list[dict]:
    return client.web_search(query, max_results=8)["results"]
```

Rows are the universal `{title, url, snippet}` shape, so existing tool wiring keeps working. moo adds `id` (a `chk_` handle you can drill into), `source_type`, `trust_score`, `relevance`, `highlights`, and `fetched_at` alongside.

Register the tool definition moo publishes instead of writing your own:

```python
tools = client.tools()["tools"]  # OpenAI-style function tools: web_search, extract
```

## Deep research with citations

```python
report = client.research("is pgbouncer still recommended over built-in pooling", max_steps=4)

print(report["executive_answer"])
for point in report["disputed_points"]:
    print("disputed:", point["text"])
```

`report["findings"]` carries per-claim confidence and citation numbers into `report["sources"]`; `report["groundedness"]` says how many findings are actually backed by a source that supports them. Pass `output_schema` (a JSON schema) for a caller-shaped `structured` section where every populated field traces back to claim handles.

Stream it instead of waiting:

```python
for event, data in client.research_stream("why is my connection pool exhausted"):
    if event == "progress":
        print(data["query"], data["reason"])
    elif event == "report":
        print(data["executive_answer"])
```

## Everything else

| Method | Endpoint | What it gives you |
| --- | --- | --- |
| `search(q, mode=...)` | `GET /search` | ranked evidence; `raw` makes zero LLM calls, `claims` adds the evidence layer, `full` adds a cited answer |
| `search_stream(q)` | `GET /search/stream` | the same, as SSE events |
| `web_search(query)` | `POST /v1/web_search` | drop-in web-search rows |
| `extract(urls)` | `POST /v1/extract` | URLs to clean markdown; `depth="claims"` runs evidence per page |
| `research(question)` | `POST /research` | cited multi-hop report |
| `research_stream(question)` | `POST /research/stream` | the same, streamed |
| `get_research(run_id)` | `GET /research/{id}` | poll or resume a run |
| `source/chunk/claim(handle)` | `GET /source|/chunk|/claim/{id}` | drill into any handle a result gave you |
| `graph(q)` / `expand_graph(node)` | `GET /graph`, `/graph/expand` | the entity subgraph behind a query |
| `account_usage()` | `GET /account/usage` | credits left, reset date, per-endpoint spend |
| `contract()` / `tools()` / `health()` / `usage()` | | introspection |

Async is the same surface, awaited:

```python
from moo import AsyncMoo

async with AsyncMoo() as client:
    results = await client.web_search("sqlite wal mode")
    async for event, data in client.research_stream("wal vs journal"):
        ...
```

## Configuration

| Argument | Environment | Default |
| --- | --- | --- |
| `base_url` | `MOO_BASE_URL` | `http://127.0.0.1:8000` |
| `api_key` | `MOO_API_KEY` | none (a self-hosted moo needs no key) |
| `timeout` | | 60s |
| `max_retries` | | 2 |

Pass your own `httpx.Client` as `http_client` to control proxies, transports, or connection pooling; the SDK will not close a client it did not create.

## Errors

Every failure is a `MooError` subclass carrying moo's structured envelope:

```python
from moo import MooError, RateLimitError

try:
    client.search("...")
except RateLimitError as e:
    wait = e.retry_after
except MooError as e:
    print(e.code, e.request_id, e.retryable)
```

429 and 5xx responses are retried automatically (honoring `Retry-After`) before they reach you; `invalid_request`, `not_found`, and `unauthorized` are raised immediately. Streams commit HTTP 200 before they can fail, so a mid-stream failure arrives as an `error` event and is raised at the point you iterate to it.

## Development

```bash
python -m pytest tests -q
```

`tests/test_contract.py` reads `api/openapi.json` and fails when moo publishes an endpoint the SDK has not mapped, so drift shows up as a red test rather than a missing method.

# llama-index-tools-moo

[moo](https://github.com/suppprith/moo) for LlamaIndex: live web search scoped to software and computer science, with claim-level evidence, trust scores, and cited deep research.

```bash
pip install llama-index-tools-moo
```

## Retriever

```python
from llama_index_moo import MooRetriever

retriever = MooRetriever(k=6)
nodes = retriever.retrieve("why is my postgres connection pool exhausted")
```

Node metadata keeps the `chk_` handle, `url`, `title`, `source_type`, `trust_score`, `fetched_at`, and `suspicious` when the page tripped moo's prompt-injection scan. The node score is moo's relevance, which is comparable across queries rather than only within one result set. Metadata is excluded from embeddings by default, so it never pollutes a vector index.

`MooRetriever(full_text=True)` fetches the complete chunk text per hit instead of the snippet, one extra request each, which is what you want when the nodes feed a query engine:

```python
from llama_index.core.query_engine import RetrieverQueryEngine

engine = RetrieverQueryEngine.from_args(MooRetriever(k=6, full_text=True))
print(engine.query("does WAL mode help concurrent readers"))
```

## Reader

```python
from llama_index_moo import MooReader

documents = MooReader().load_data(["https://www.sqlite.org/wal.html"])
```

Clean markdown per page. URLs are batched to moo's limit of ten per request, pages inside their cache TTL come back without touching the network, and a URL that fails is skipped rather than failing the batch.

## Agent tools

```python
from llama_index.core.agent.workflow import FunctionAgent
from llama_index_moo import MooToolSpec

agent = FunctionAgent(tools=MooToolSpec().to_tool_list(), llm=llm)
```

| Tool | What the model gets |
| --- | --- |
| `moo_search` | ranked live results with title, url, snippet and a trust score |
| `moo_extract` | specific URLs as clean markdown, with per-URL failures |
| `moo_deep_research` | a cited report: the answer, where sources disagree, what stayed unanswered |

## Configuration

Everything reads `MOO_BASE_URL` (default `http://127.0.0.1:8000`) and `MOO_API_KEY` from the environment. Pass a configured client to override:

```python
from moo import Moo

client = Moo(api_key="sk-...", base_url="https://api.example.com")
retriever = MooRetriever(client, k=8, depth="claims")
tools = MooToolSpec(client).to_tool_list()
```

`depth="claims"` attaches moo's evidence layer (claims, confidence, contradictions) at the cost of LLM calls on moo's side; the default `raw` makes none.

## A note on untrusted content

Retrieved pages are third-party data, not instructions. moo scans every page for prompt-injection patterns, marks matches rather than dropping them, and halves the source's trust. The flag rides along as `suspicious` in node metadata and is stated inline in rendered tool output, so a model can see it.

## Development

```bash
pip install llama-index-core httpx pytest
pip install -e ../../sdk/python
python -m pytest tests -q
```

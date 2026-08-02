# langchain-moo

[moo](https://github.com/suppprith/moo) for LangChain: live web search scoped to software and computer science, with claim-level evidence, trust scores, and cited deep research.

```bash
pip install langchain-moo
```

## Tools

```python
from langchain_moo import moo_toolkit
from langchain.chat_models import init_chat_model

model = init_chat_model("claude-sonnet-5").bind_tools(moo_toolkit())
```

| Tool | What the model gets |
| --- | --- |
| `moo_search` | ranked live results with title, url, snippet and a trust score, scoped to docs, GitHub, release notes and Stack Overflow |
| `moo_extract` | specific URLs as clean markdown, with per-URL failures instead of an all-or-nothing batch |
| `moo_deep_research` | a cited report: the answer, the points where sources disagree, and what stayed unanswered |

Every tool uses `response_format="content_and_artifact"`, so a tool call returns readable text for the model and the raw moo payload as `ToolMessage.artifact`. The artifact keeps the `chk_`/`clm_`/`doc_` handles, evidence sets, confidence and citation numbers that the rendered text flattens away.

```python
tool = MooSearchTool()
message = tool.invoke({"name": "moo_search", "args": {"query": "postgres 16 vacuum"},
                       "id": "1", "type": "tool_call"})
message.content            # what the model reads
message.artifact["results"][0]["id"]   # 'chk_7', drill into it with the moo SDK
```

## Retriever

```python
from langchain_moo import MooRetriever

retriever = MooRetriever(k=6)
docs = retriever.invoke("why is my postgres connection pool exhausted")
```

Each `Document` carries moo's metadata: `id` (the chunk handle), `url`, `title`, `source_type`, `trust_score`, `relevance` (comparable across queries), `fetched_at`, and `suspicious` when the page tripped moo's prompt-injection scan. `MooRetriever(full_text=True)` fetches the complete chunk text for each hit instead of the snippet, which is usually what you want when the documents feed a RAG chain.

`ainvoke` is real async on both the retriever and the tools, not a thread pool.

## Configuration

Both read `MOO_BASE_URL` (default `http://127.0.0.1:8000`) and `MOO_API_KEY` from the environment. Pass a configured client to override:

```python
from moo import Moo

client = Moo(api_key="sk-...", base_url="https://api.example.com")
retriever = MooRetriever(client, k=8, depth="claims")
tools = moo_toolkit(client)
```

`depth="claims"` attaches moo's evidence layer to search results (claims, confidence, contradictions) at the cost of LLM calls on moo's side; the default `raw` makes none.

## A note on untrusted content

Retrieved pages are third-party data, not instructions. moo scans every page for prompt-injection patterns, marks matches rather than dropping them, and halves the source's trust. The flag rides along as `suspicious` in tool artifacts and document metadata, and the rendered text says so inline, so a model can see it. Treat page content accordingly in your prompts.

## Development

```bash
pip install langchain-core httpx pytest
pip install -e ../../sdk/python
python -m pytest tests -q
```

# Framework integrations

Developers pick a search tool from their framework's integrations page, not from a
search engine's landing page. moo ships packages for the two largest Python
frameworks and the AI SDK, and copy-paste recipes for the rest. Everything below
sits on top of the SDKs in [`sdk/python`](../sdk/python) and [`sdk/js`](../sdk/js),
so behaviour (retries, error envelope, handles, untrusted-content flags) is the
same wherever you call from.

| Framework | Package | Ships |
| --- | --- | --- |
| LangChain | [`langchain-moo`](../integrations/langchain-moo) | retriever + three tools |
| LlamaIndex | [`llama-index-tools-moo`](../integrations/llamaindex-moo) | retriever + reader + tool spec |
| Vercel AI SDK | [`@moo/ai-sdk`](../integrations/vercel-ai) | three tools |
| CrewAI | recipe below | one tool class |
| OpenAI Agents SDK | recipe below | three function tools |
| Raw OpenAI tool-use loop | `GET /v1/tools` | the function-tool definitions themselves |

Every integration exposes the same three capabilities, and the descriptions the
model reads are deliberately identical across frameworks:

- **search**: ranked live results with a trust score, scoped to software sources
- **extract**: specific URLs as clean markdown, failures reported per URL
- **deep research**: a cited report, plus the points where sources disagree

## CrewAI

CrewAI tools are pydantic-shaped classes, so the wrapper is a few lines over the
Python SDK.

```python
from crewai.tools import BaseTool
from moo import Moo
from pydantic import BaseModel, Field

client = Moo()


class SearchInput(BaseModel):
    query: str = Field(description="the search query")


class MooSearchTool(BaseTool):
    name: str = "moo_search"
    description: str = (
        "Search the web for software-engineering and computer-science questions. "
        "Returns ranked results with title, url and snippet, fetched live from docs, "
        "GitHub, release notes and Stack Overflow, each with a trust score. Prefer "
        "this over a general web search for coding questions."
    )
    args_schema: type[BaseModel] = SearchInput

    def _run(self, query: str) -> str:
        payload = client.web_search(query, max_results=8)
        return "\n".join(
            f"[{n}] {row['title']} (trust {row.get('trust_score')})\n{row['url']}\n{row['snippet']}"
            for n, row in enumerate(payload["results"], 1)
        )
```

Swap `client.web_search` for `client.research(question)` and render
`executive_answer` plus `disputed_points` to get the deep-research tool. The
rendering helpers in `langchain_moo._shared` are worth copying if you want the
same output shape.

## OpenAI Agents SDK

```python
from agents import Agent, function_tool
from moo import Moo

client = Moo()


@function_tool
def moo_search(query: str, max_results: int = 8) -> str:
    """Search the web for software-engineering and computer-science questions.
    Returns ranked live results with a trust score. Prefer this over a general
    web search for coding questions."""
    payload = client.web_search(query, max_results=max_results)
    return "\n".join(f"{row['title']} - {row['url']}\n{row['snippet']}"
                     for row in payload["results"])


@function_tool
def moo_deep_research(question: str) -> str:
    """Research a software question end to end and return a cited answer, plus
    the points where sources disagree."""
    report = client.research(question, max_steps=6)
    disputed = "\n".join(f"- {point['text']}" for point in report["disputed_points"])
    return f"{report['executive_answer']}\n\nWhere sources disagree:\n{disputed}"


agent = Agent(name="engineer", tools=[moo_search, moo_deep_research])
```

## Any OpenAI-compatible tool loop

moo publishes its own function-tool definitions, so there is nothing to write:

```bash
curl -s localhost:8000/v1/tools
```

Register the definitions and execute calls against `POST /v1/web_search` and
`POST /v1/extract`. Pass `"shape": "anthropic"` to get `web_search_result`
content blocks instead of rows.

## Untrusted content, everywhere

Retrieved pages are third-party data. moo scans every page for prompt-injection
patterns, marks matches rather than dropping them, and halves the source's trust.
Each integration passes the flag through: `suspicious` in document or node
metadata and in raw payloads, and an inline note in rendered tool output. Keep it
visible to the model, and keep treating page content as data rather than
instructions.

## Getting listed

The packages are the easy half. The listing is the point: a merged entry in a
framework's integrations docs is the top-of-funnel that landing pages are not.
For each framework that means a docs PR to their repo with a short page, a
runnable snippet, and the published package name, which is why publishing the
SDKs comes first. Track the submissions here as they land.

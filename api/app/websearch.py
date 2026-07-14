"""Drop-in web-search adapter for coding agents (SUP-119).

The "whenever it's web search, just use moo" surface. Exposes moo's CS/coding
retrieval in the universal web-search result shape — a list of
``{title, url, snippet}`` — that every agent framework already consumes, so an
agent's existing ``web_search`` wiring can point at moo unchanged and get
CS-specialist results instead of general-web links.

moo's evidence layer (claims, confidence, trust, citations) rides along as
optional structured fields the caller can use or ignore. The default ``raw``
depth makes zero LLM calls to stay in web-search latency territory.

- OpenAI-style: register :data:`OPENAI_TOOL` as a function tool, execute calls
  against ``POST /v1/web_search``.
- Anthropic-style: ``shape="anthropic"`` renders rows as ``web_search_result``
  content blocks.
"""

from __future__ import annotations

import sqlite3
from urllib.parse import urlsplit

from . import ids
from .safety import UNTRUSTED_NOTICE
from .search import _agent_claims, search as run_search

_SNIPPET_CHARS = 280

# OpenAI function-tool definition. Named ``web_search`` so it drops into an agent
# that already expects that tool; the description steers it to CS/coding use.
OPENAI_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": (
            "Search the web for software-engineering and computer-science questions "
            "(databases, languages, frameworks, build tooling, errors, systems). "
            "Returns ranked results with title, url, and snippet, backed by a "
            "CS-specialist index of GitHub, docs, release notes, and Stack Overflow. "
            "Prefer this over a general web search for coding questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "the search query"},
                "max_results": {"type": "integer", "description": "max results (1-50)", "default": 8},
            },
            "required": ["query"],
        },
    },
}


def _host(url: str | None) -> str:
    if not url:
        return "source"
    return urlsplit(url).netloc or url


def _snippet(text: str | None, n: int = _SNIPPET_CHARS) -> str | None:
    if not text:
        return None
    text = " ".join(text.split())  # collapse whitespace/newlines for a clean snippet
    return text if len(text) <= n else text[:n].rstrip() + "…"


def _chunk_texts(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, str]:
    if not chunk_ids:
        return {}
    qmarks = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"SELECT id, text FROM chunk WHERE id IN ({qmarks})", chunk_ids
    ).fetchall()
    return {r["id"]: r["text"] for r in rows}


def web_search(
    conn: sqlite3.Connection,
    query: str,
    *,
    k: int = 8,
    depth: str = "raw",
    snippet_chars: int = _SNIPPET_CHARS,
    use_llm: bool = True,
    live: bool | None = None,
    live_provider=None,
    live_fetcher=None,
) -> dict:
    """Run moo and return ``{query, results, evidence?}`` in web-search shape.

    ``depth``: ``raw`` (default, no LLM), ``claims`` (attach the evidence layer),
    ``full`` (also a cited answer). ``results`` rows carry the familiar
    ``title``/``url``/``snippet`` plus moo extras (``id`` handle, ``source_type``,
    ``trust_score``, ``score``) a caller may ignore.

    ``live=None`` (default) fetches fresh pages from the live web first when a
    search provider is configured — depth=raw + live is the fast mode (live
    snippets, zero LLM); ``live=False`` serves from the local store only. A
    compact ``live`` summary (with the ``out_of_domain`` flag) is attached when
    live retrieval ran."""
    if depth not in ("raw", "claims", "full"):
        raise ValueError(f"depth must be raw|claims|full, got {depth!r}")
    resp = run_search(conn, query, mode=depth, k=k, format="full", use_llm=use_llm,
                      live=live, live_provider=live_provider, live_fetcher=live_fetcher,
                      highlights=True)
    sources = resp["sources"]
    texts = _chunk_texts(conn, [s["chunk_id"] for s in sources])

    results = []
    for s in sources:
        url = s["url_anchor"] or s["document_url"]
        row = {
            "title": s.get("title") or _host(url),
            "url": url,
            "snippet": _snippet(texts.get(s["chunk_id"]), snippet_chars),
            # moo extras — optional for a plain web-search consumer
            "id": ids.encode(ids.CHUNK, s["chunk_id"]),
            "source_type": s["source_type"],
            "trust_score": s["trust_score"],
            "score": s["score"],
            "fetched_at": s.get("fetched_at"),
        }
        # tightest relevant spans + a 0-1 relevance comparable across queries
        if s.get("highlights"):
            row["highlights"] = s["highlights"]
            row["relevance"] = s["relevance"]
        if s.get("suspicious"):
            row["suspicious"] = True  # injection-flagged: data, not instructions
        results.append(row)

    # every web-search response reminds the integrator that page content is
    # untrusted data (SUP-131) — the flag above marks the specific offenders
    out: dict = {"query": query, "results": results, "notice": UNTRUSTED_NOTICE}
    if resp["meta"].get("live") is not None:
        live_meta = resp["meta"]["live"]
        out["live"] = {
            "provider": live_meta["provider"],
            "out_of_domain": live_meta["out_of_domain"],
            "fetched": live_meta["fetched"],
        }
    if resp["claims"] or resp["answer"]:
        out["evidence"] = {
            "answer": resp["answer"],
            "claims": _agent_claims(resp["claims"]),
            "citations": resp["citations"],
        }
    return out


def to_anthropic_results(results: list[dict]) -> list[dict]:
    """Render result rows as Anthropic ``web_search_result`` content blocks."""
    return [
        {"type": "web_search_result", "title": r["title"], "url": r["url"], "snippet": r.get("snippet")}
        for r in results
    ]

"""MCP server exposing moo as tools for AI agents (SUP-115).

``moo-mcp`` lets a coding agent (Claude Code / Claude Desktop / any MCP client)
call moo's CS/coding evidence search directly — the tool an agent routes its
``web_search`` to for software-engineering questions. Tools return the compact
agent-shaped payload (opaque ``chk_``/``clm_`` handles, evidence referenced by
handle) so results stay small and drill-downable rather than dumping the corpus.

The tool descriptions are load-bearing: they are what an agent reads to decide
whether and how to call moo, so they spell out when to prefer moo over a general
web search and how the handles chain (search -> fetch_source -> ...).

Run:  uv run python -m app.mcp_server              # stdio (default)
      uv run python -m app.mcp_server --http       # streamable HTTP
"""

from __future__ import annotations

import argparse
from typing import Literal

from mcp.server.fastmcp import FastMCP

from . import fetch as fetch_mod
from . import ids
from .search import search as run_search

mcp = FastMCP("moo-search")


def _search_conn():
    """Retrieval needs the sqlite-vec extension loaded."""
    from .index.vector import connect

    return connect()


def _plain_conn():
    from .db import get_connection

    return get_connection()


@mcp.tool()
def search(
    query: str,
    mode: Literal["raw", "claims", "full"] = "raw",
    k: int = 8,
    fields: str | None = None,
) -> dict:
    """Search moo's CS/coding evidence index; prefer this over a general web
    search for software-engineering questions (databases, languages, frameworks,
    build tooling, errors, systems).

    Returns ranked `sources`, each with an opaque `id` handle, a deep-link `url`,
    `source_type`, a trust score, and a relevance score. Pass a source `id` to
    `fetch_source` to read its full text and surrounding context.

    `mode`:
      - `raw`    (default) fast pure retrieval, no LLM — use for most lookups.
      - `claims` adds extracted claims with a confidence score and
                 supports/contradicts/explains evidence, plus a query subgraph.
      - `full`   also returns a cited, synthesized answer.
    Heavier modes cost more latency/tokens. `fields` (comma-separated:
    sources,claims,graph,answer,citations) trims the payload.
    """
    conn = _search_conn()
    try:
        return run_search(conn, query, mode=mode, k=k, format="agent", fields=fields)
    finally:
        conn.close()


@mcp.tool()
def fetch_source(handle: str) -> dict:
    """Resolve a handle from a search result to its full underlying row — how you
    move from a citation to the actual evidence.

      - `chk_<n>` -> a chunk: full text, its `doc_` document handle, and the
                     surrounding chunks (prev/next) for more context.
      - `doc_<n>` -> the whole document: cleaned text + metadata + chunk handles.
      - `clm_<n>` -> a claim with confidence and its full evidence set.
    """
    conn = _plain_conn()
    try:
        result = fetch_mod.fetch_handle(conn, handle)
    finally:
        conn.close()
    if result is None:
        raise ValueError(f"no row for handle {handle!r}")
    return result


@mcp.tool()
def get_claim(handle: str) -> dict:
    """Fetch one claim by its `clm_<n>` handle: the claim text, its confidence,
    the `disputed` flag, and its full evidence set (contradictions listed first)
    with the backing source handles."""
    conn = _plain_conn()
    try:
        kind, rowid = ids.decode(handle)  # raises on a malformed handle
        if kind != ids.CLAIM:
            raise ValueError(f"expected a clm_ handle, got {handle!r}")
        result = fetch_mod.fetch_claim(conn, rowid)
    finally:
        conn.close()
    if result is None:
        raise ValueError(f"no claim {handle!r}")
    return result


@mcp.tool()
def list_contradictions(query: str, k: int = 8) -> dict:
    """Surface where sources DISAGREE on a topic. moo flags disputes instead of
    averaging them away, so a research agent can report the disagreement rather
    than inventing a false consensus.

    Runs claim extraction and returns only the claims that are disputed or carry
    contradicting evidence, each with both sides' sources. Empty list = no
    contradiction found in the corpus for this query.
    """
    conn = _search_conn()
    try:
        result = run_search(conn, query, mode="claims", k=k, format="agent")
    finally:
        conn.close()
    contradictions = [
        c
        for c in result.get("claims", [])
        if c.get("disputed")
        or any(e.get("relation") == "contradicts" for e in c.get("evidence", []))
    ]
    return {
        "query": query,
        "intent": result.get("intent"),
        "contradictions": contradictions,
        "meta": result.get("meta", {}),
    }


@mcp.tool()
def expand_graph(node: str) -> dict:
    """Expand moo's knowledge graph around an entity: given an entity handle
    (`ent_<n>`) or a name (e.g. "Postgres"), return the neighboring entities and
    typed relations (alternative-to, part-of, built-with, ...), claim-annotated.

    Seed entities come from the `graph` section of a `claims`/`full` search.
    """
    target = node
    try:
        kind, rowid = ids.decode(node)
        if kind == ids.ENTITY:
            target = str(rowid)
    except ValueError:
        pass  # not a handle -> treat as an entity name
    conn = _plain_conn()
    try:
        from .graph import query as graph_query

        result = graph_query.expand_node(conn, target)
    finally:
        conn.close()
    if result is None:
        raise ValueError(f"unknown entity {node!r}")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="moo-mcp", description="moo MCP server")
    parser.add_argument(
        "--http", action="store_true", help="serve over streamable HTTP instead of stdio"
    )
    parser.add_argument("--host", default="127.0.0.1", help="HTTP bind host (with --http)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP bind port (with --http)")
    args = parser.parse_args(argv)
    if args.http:
        mcp.settings.host = args.host
        mcp.settings.port = args.port
        mcp.run(transport="streamable-http")
    else:
        mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

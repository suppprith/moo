"""moo search API entrypoint.

Run locally with:  uv run fastapi dev app/main.py
"""

from typing import Any, Literal

import os

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .db import get_connection
from .graph import query as graph_query
from .search import CONTRACT_VERSION, decode_cursor, search

app = FastAPI(
    title="moo search API",
    version="0.1.0",
    summary="Evidence-graph search engine",
)

# Let the web/ dev server (and a self-hosted UI) call the API from the browser.
# Override the allowed origins with MOO_CORS_ORIGINS (comma-separated) in prod.
_origins = os.environ.get(
    "MOO_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins if o.strip()],
    allow_methods=["GET"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# /search response contract (versioned, documented via OpenAPI)
# ---------------------------------------------------------------------------

class Source(BaseModel):
    chunk_id: int
    document_url: str
    title: str | None = None
    source_type: str
    trust_score: float | None = None
    heading: str | None = None
    url_anchor: str
    score: float


class Evidence(BaseModel):
    relation: Literal["supports", "contradicts", "explains"]
    chunk_id: int
    strength: float | None = None
    document_url: str | None = None


class Claim(BaseModel):
    id: int
    text: str
    confidence: float | None = None
    disputed: bool = False
    evidence: list[Evidence] = Field(default_factory=list)


class SearchResponse(BaseModel):
    """The ``format=full`` contract (the UI shape). ``format=agent`` returns a
    compact projection with opaque handles instead — its strict schema is
    formalized in SUP-109, so the endpoint returns a plain dict rather than
    pinning one response_model across both shapes."""

    query: str
    mode: Literal["raw", "claims", "full"]
    intent: str
    answer: str | None = None
    claims: list[Claim] = Field(default_factory=list)
    graph: dict[str, Any] = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "contract_version": CONTRACT_VERSION}


@app.get("/search")
def search_endpoint(
    q: str = Query(..., description="the search query"),
    mode: Literal["raw", "claims", "full"] = Query(
        "raw", description="raw=pure retrieval (no LLM); claims=+evidence; full=+synthesis"
    ),
    k: int = Query(10, ge=1, le=50),
    format: Literal["full", "agent"] = Query(
        "full", description="full=UI contract; agent=compact, opaque handles, evidence-by-reference"
    ),
    fields: str | None = Query(
        None, description="comma-separated sections to include: sources,claims,graph,answer,citations"
    ),
    offset: int = Query(0, ge=0, le=200, description="pagination offset into the sources list"),
    cursor: str | None = Query(
        None, description="opaque pagination cursor from meta.page.next_cursor (overrides offset)"
    ),
) -> dict:
    """Unified pipeline. `mode=raw` (default) makes zero LLM calls; `claims` adds
    the evidence layer + query subgraph; `full` adds a cited synthesized answer.

    `format=agent` returns a compact, handle-addressed payload for AI agents;
    `fields` selects sections; `offset`/`cursor` paginate `sources`."""
    if cursor is not None:
        try:
            offset = decode_cursor(cursor)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    conn = get_connection_for_search()
    try:
        return search(conn, q, mode=mode, k=k, format=format, fields=fields, offset=offset)
    except ValueError as e:  # bad fields/format/offset
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        conn.close()


def get_connection_for_search():
    """Search needs the sqlite-vec extension loaded for vector retrieval."""
    from .index.vector import connect

    return connect()


@app.get("/graph")
def graph(
    q: str = Query(..., description="natural-language query"),
    depth: int = Query(1, ge=1, le=3),
    cap: int = Query(60, ge=1, le=300),
) -> dict:
    """Subgraph-for-query: seed entities from the query, their neighborhood, and
    the claims attached to each node/edge. Sized for rendering (capped,
    expandable via /graph/expand)."""
    conn = get_connection()
    try:
        return graph_query.graph_for_query(conn, q, depth=depth, cap=cap)
    finally:
        conn.close()


@app.get("/graph/expand")
def graph_expand(
    node: str = Query(..., description="entity id or name"),
    limit: int | None = Query(None, ge=1, le=100),
) -> dict:
    """The next ring of edges for a node, for click-to-expand in the explorer."""
    conn = get_connection()
    try:
        result = graph_query.expand_node(conn, node, limit=limit)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(status_code=404, detail=f"unknown entity: {node!r}")
    return result

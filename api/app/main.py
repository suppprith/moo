"""moo search API entrypoint.

Run locally with:  uv run fastapi dev app/main.py
"""

from fastapi import FastAPI, HTTPException, Query

from .db import get_connection
from .graph import query as graph_query

app = FastAPI(
    title="moo search API",
    version="0.1.0",
    summary="Evidence-graph search engine",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok"}


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

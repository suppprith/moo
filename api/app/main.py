"""moo search API entrypoint.

Run locally with:  uv run fastapi dev app/main.py
"""

from typing import Any, Literal

import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import ids
from .db import get_connection
from .errors import ApiError, code_for_status, envelope, new_request_id
from .fetch import fetch_chunk, fetch_claim, fetch_document
from .graph import query as graph_query
from .search import CONTRACT_VERSION, decode_cursor, search
from .streaming import sse_event, sse_response
from .websearch import OPENAI_TOOL, to_anthropic_results, web_search

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
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# ---------------------------------------------------------------------------
# Request IDs + structured error envelope (SUP-107)
# ---------------------------------------------------------------------------

@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Tag every request with an id echoed in the response header and in any
    error envelope, so a failing call is traceable end to end."""
    request.state.request_id = new_request_id()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_request_id()


@app.exception_handler(ApiError)
async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    rid = _request_id(request)
    return JSONResponse(
        status_code=exc.status,
        content=envelope(exc.code, exc.message, exc.retryable, rid),
        headers={"X-Request-ID": rid},
    )


@app.exception_handler(HTTPException)
async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    rid = _request_id(request)
    code = code_for_status(exc.status_code)
    retryable = code in ("timeout", "upstream_error", "internal", "rate_limited")
    return JSONResponse(
        status_code=exc.status_code,
        content=envelope(code, str(exc.detail), retryable, rid),
        headers={"X-Request-ID": rid},
    )


@app.exception_handler(RequestValidationError)
async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    rid = _request_id(request)
    msg = "; ".join(f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors())
    return JSONResponse(
        status_code=422,
        content=envelope("invalid_request", msg or "invalid request", False, rid),
        headers={"X-Request-ID": rid},
    )


@app.exception_handler(Exception)
async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    rid = _request_id(request)
    return JSONResponse(
        status_code=500,
        content=envelope("internal", "internal server error", True, rid),
        headers={"X-Request-ID": rid},
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


@app.get("/search/stream")
def search_stream_endpoint(
    q: str = Query(..., description="the search query"),
    mode: Literal["raw", "claims", "full"] = Query("raw"),
    k: int = Query(10, ge=1, le=50),
    fields: str | None = Query(None, description="comma-separated sections to include"),
) -> Any:
    """Streaming (SSE) variant of /search: emits a `progress` event, then one
    `source` (and `claim`) event per row, then a terminal `done`. A failure
    mid-stream arrives as a terminal `error` event (see app.streaming)."""

    def frames():
        conn = None
        try:
            conn = get_connection_for_search()
            yield sse_event("progress", {"stage": "searching", "query": q, "mode": mode})
            result = search(conn, q, mode=mode, k=k, format="agent", fields=fields)
            for s in result.get("sources", []):
                yield sse_event("source", s)
            for c in result.get("claims", []):
                yield sse_event("claim", c)
            done: dict[str, Any] = {"mode": result["mode"], "intent": result["intent"], "meta": result["meta"]}
            if result.get("answer"):
                done["answer"] = result["answer"]
            yield sse_event("done", done)
        except Exception as exc:  # noqa: BLE001 - headers already sent; report in-band
            rid = new_request_id()
            if isinstance(exc, ApiError):
                env = envelope(exc.code, exc.message, exc.retryable, rid)
            elif isinstance(exc, ValueError):
                env = envelope("invalid_request", str(exc), False, rid)
            else:
                env = envelope("internal", "search failed", True, rid)
            yield sse_event("error", env)
        finally:
            if conn is not None:
                conn.close()

    return sse_response(frames())


def get_connection_for_search():
    """Search needs the sqlite-vec extension loaded for vector retrieval."""
    from .index.vector import connect

    return connect()


# ---------------------------------------------------------------------------
# Drop-in web_search adapter for coding agents (SUP-119)
# ---------------------------------------------------------------------------

class WebSearchRequest(BaseModel):
    query: str
    max_results: int = Field(8, ge=1, le=50)
    depth: Literal["raw", "claims", "full"] = "raw"
    shape: Literal["web", "anthropic"] = "web"


def _run_web_search(query: str, max_results: int, depth: str, shape: str) -> dict:
    conn = get_connection_for_search()
    try:
        out = web_search(conn, query, k=max_results, depth=depth)
    finally:
        conn.close()
    if shape == "anthropic":
        out["results"] = to_anthropic_results(out["results"])
    return out


@app.post("/v1/web_search")
def web_search_post(req: WebSearchRequest) -> dict:
    """Drop-in web search for coding agents: point your agent's `web_search` tool
    here and get CS-specialist `{title, url, snippet}` results (plus moo's
    evidence layer as optional fields). `depth=raw` (default) makes zero LLM
    calls. Register `GET /v1/tools` as the tool definition."""
    return _run_web_search(req.query, req.max_results, req.depth, req.shape)


@app.get("/v1/web_search")
def web_search_get(
    q: str = Query(..., description="the search query"),
    max_results: int = Query(8, ge=1, le=50),
    depth: Literal["raw", "claims", "full"] = Query("raw"),
    shape: Literal["web", "anthropic"] = Query("web"),
) -> dict:
    """GET convenience form of the drop-in web search (see POST /v1/web_search)."""
    return _run_web_search(q, max_results, depth, shape)


@app.get("/v1/tools")
def web_search_tools() -> dict:
    """The OpenAI-style function-tool definition to register so an agent routes
    its `web_search` to moo; execute the calls against POST /v1/web_search."""
    return {"tools": [OPENAI_TOOL]}


# ---------------------------------------------------------------------------
# Fetch / drill-down: resolve a citation handle to the underlying row (SUP-106)
# ---------------------------------------------------------------------------

def _rowid(id: str, expected_kind: str) -> int:
    """Accept either an opaque handle (``chk_75``) — validated against the
    endpoint's kind — or a bare rowid (``75``)."""
    try:
        kind, rowid = ids.decode(id)
    except ValueError:
        if id.isdigit():
            return int(id)
        raise HTTPException(status_code=422, detail=f"malformed id: {id!r}")
    if kind != expected_kind:
        raise HTTPException(
            status_code=422, detail=f"expected a {expected_kind!r} handle, got {id!r}"
        )
    return rowid


@app.get("/source/{id}")
def source_endpoint(id: str) -> dict:
    """Full document behind a source: cleaned text + metadata + chunk handles.
    Accepts a `doc_<id>` handle or bare rowid."""
    rowid = _rowid(id, ids.DOCUMENT)
    conn = get_connection()
    try:
        result = fetch_document(conn, rowid)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(status_code=404, detail=f"no document {id!r}")
    return result


@app.get("/chunk/{id}")
def chunk_endpoint(id: str) -> dict:
    """A chunk with full text, its document handle, and surrounding-chunk context.
    Accepts a `chk_<id>` handle (as emitted in agent-format sources) or bare rowid."""
    rowid = _rowid(id, ids.CHUNK)
    conn = get_connection()
    try:
        result = fetch_chunk(conn, rowid)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(status_code=404, detail=f"no chunk {id!r}")
    return result


@app.get("/claim/{id}")
def claim_endpoint(id: str) -> dict:
    """A claim with confidence + its full evidence set (contradictions first) and
    extraction provenance. Accepts a `clm_<id>` handle or bare rowid."""
    rowid = _rowid(id, ids.CLAIM)
    conn = get_connection()
    try:
        result = fetch_claim(conn, rowid)
    finally:
        conn.close()
    if result is None:
        raise HTTPException(status_code=404, detail=f"no claim {id!r}")
    return result


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

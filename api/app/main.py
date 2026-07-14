"""moo API entrypoint.

Run locally with:  uv run fastapi dev app/main.py
"""

from typing import Any, Literal

import os

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

import queue
import threading

from . import auth, ids
from .db import get_connection
from .errors import ApiError, code_for_status, envelope, new_request_id
from .extract import MAX_URLS as EXTRACT_MAX_URLS, extract_urls
from .fetch import fetch_chunk, fetch_claim, fetch_document
from .graph import query as graph_query
from .research import session as research_session
from .research.loop import run_loop
from .research.plan import plan as make_plan
from .research.report import assemble_report
from .search import CONTRACT_VERSION, decode_cursor, search
from .streaming import sse_event, sse_response
from .websearch import OPENAI_TOOL, to_anthropic_results, web_search

app = FastAPI(
    title="moo",
    version="0.1.0",
    summary="CS/coding evidence search for AI agents",
    description=(
        "CS/coding-specialized search backend for AI agents. Endpoints: `/search` "
        "(ranked evidence), `/v1/web_search` (drop-in web_search shape), `/research` "
        "(deep research), fetch/drill-down (`/source|/chunk|/claim/{id}`), `/graph`. "
        "Opaque handles: `chk_`/`doc_`/`clm_`/`ent_`. Errors use a structured "
        "envelope with a stable `code`. See `/contract` for the machine-readable "
        "descriptor and the MCP tool schemas."
    ),
    license_info={"name": "MIT", "url": "https://github.com/suppprith/moo/blob/main/LICENSE"},
)


@app.on_event("startup")
def _startup() -> None:
    # zero-setup first run (SUP-159): create/migrate the DB + log config hints
    from .bootstrap import ensure_ready

    ensure_ready()

# Let the web/ dev server (and a self-hosted UI) call the API from the browser.
# Override the allowed origins with MOO_CORS_ORIGINS (comma-separated) in prod.
_origins = os.environ.get(
    "MOO_CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000,http://localhost:3100,http://127.0.0.1:3100",
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

_AUTH_EXEMPT = {"/", "/health", "/contract", "/v1/tools", "/openapi.json", "/docs", "/redoc"}


@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    """Tag every request with an id echoed in the response header and in any
    error envelope, so a failing call is traceable end to end. Also enforces
    optional API-key auth + per-key rate limiting (SUP-108)."""
    request.state.request_id = new_request_id()
    path = request.url.path
    if auth.enabled() and path not in _AUTH_EXEMPT and not path.startswith(("/docs", "/openapi")):
        try:
            auth.check(request)
        except ApiError as exc:
            headers = {"X-Request-ID": request.state.request_id}
            if exc.retry_after is not None:
                headers["Retry-After"] = str(exc.retry_after)
            return JSONResponse(
                status_code=exc.status,
                content=envelope(exc.code, exc.message, exc.retryable, request.state.request_id),
                headers=headers,
            )
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or new_request_id()


@app.exception_handler(ApiError)
async def _handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
    rid = _request_id(request)
    headers = {"X-Request-ID": rid}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        status_code=exc.status,
        content=envelope(exc.code, exc.message, exc.retryable, rid),
        headers=headers,
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
def health() -> dict:
    """Liveness probe."""
    return {"status": "ok", "contract_version": CONTRACT_VERSION, "auth": auth.enabled()}


@app.get("/usage")
def usage() -> dict:
    """Per-key request counts (masked). Requires a valid key when auth is on."""
    return {"enabled": auth.enabled(), "usage": auth.usage()}


@app.get("/contract")
async def contract() -> dict:
    """Machine-readable descriptor: contract version, the OpenAPI URL, the handle
    formats, and the MCP tool schemas — one place for an agent to introspect moo."""
    from .mcp_server import mcp as mcp_server

    tools = await mcp_server.list_tools()
    return {
        "name": "moo",
        "contract_version": CONTRACT_VERSION,
        "openapi": "/openapi.json",
        "handle_formats": {
            "chunk": "chk_<id>", "document": "doc_<id>",
            "claim": "clm_<id>", "entity": "ent_<id>",
        },
        "error_envelope": {"error": {"code": "string", "message": "string",
                                     "retryable": "bool", "request_id": "string"}},
        "mcp_tools": [
            {"name": t.name, "description": t.description, "input_schema": t.inputSchema}
            for t in tools
        ],
    }


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
    live: bool | None = Query(
        None,
        description="fetch fresh pages from the live web first (default: auto when a "
        "search provider is configured; false = local store only)",
    ),
    highlights: bool = Query(
        False, description="add best-span highlights + 0-1 relevance to each source"
    ),
) -> dict:
    """Unified pipeline. `mode=raw` (default) makes zero LLM calls; `claims` adds
    the evidence layer + query subgraph; `full` adds a cited synthesized answer.
    With a search provider configured, results come from the live web (fast mode
    = live snippets, deep modes = live + evidence); `meta.live` reports it.

    `format=agent` returns a compact, handle-addressed payload for AI agents;
    `fields` selects sections; `offset`/`cursor` paginate `sources`."""
    if cursor is not None:
        try:
            offset = decode_cursor(cursor)
        except ValueError as e:
            raise HTTPException(status_code=422, detail=str(e))
    conn = get_connection_for_search()
    try:
        return search(conn, q, mode=mode, k=k, format=format, fields=fields, offset=offset,
                      live=live, highlights=highlights)
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
    live: bool | None = None


def _run_web_search(
    query: str, max_results: int, depth: str, shape: str, live: bool | None = None
) -> dict:
    conn = get_connection_for_search()
    try:
        out = web_search(conn, query, k=max_results, depth=depth, live=live)
    finally:
        conn.close()
    if shape == "anthropic":
        out["results"] = to_anthropic_results(out["results"])
    return out


@app.post("/v1/web_search")
def web_search_post(req: WebSearchRequest) -> dict:
    """Drop-in web search for coding agents: point your agent's `web_search` tool
    here and get software-specialist `{title, url, snippet}` results fetched
    live from the web (plus moo's evidence layer as optional fields).
    `depth=raw` (default) makes zero LLM calls. Register `GET /v1/tools` as the
    tool definition."""
    return _run_web_search(req.query, req.max_results, req.depth, req.shape, req.live)


@app.get("/v1/web_search")
def web_search_get(
    q: str = Query(..., description="the search query"),
    max_results: int = Query(8, ge=1, le=50),
    depth: Literal["raw", "claims", "full"] = Query("raw"),
    shape: Literal["web", "anthropic"] = Query("web"),
    live: bool | None = Query(None, description="live web fetch (default auto; false = store only)"),
) -> dict:
    """GET convenience form of the drop-in web search (see POST /v1/web_search)."""
    return _run_web_search(q, max_results, depth, shape, live)


@app.get("/v1/tools")
def web_search_tools() -> dict:
    """The OpenAI-style function-tool definitions to register with an agent:
    `web_search` (execute against POST /v1/web_search) and `extract` (execute
    against POST /v1/extract)."""
    from .extract import OPENAI_TOOL as EXTRACT_TOOL

    return {"tools": [OPENAI_TOOL, EXTRACT_TOOL]}


# ---------------------------------------------------------------------------
# /v1/extract: URL(s) -> clean markdown + optional evidence (SUP-147)
# ---------------------------------------------------------------------------

class ExtractRequest(BaseModel):
    urls: list[str] = Field(..., min_length=1, max_length=EXTRACT_MAX_URLS)
    depth: Literal["raw", "claims"] = "raw"
    force: bool = Field(False, description="bypass the TTL cache and re-fetch")


@app.post("/v1/extract")
def extract_post(req: ExtractRequest) -> dict:
    """Fetch URL(s) and return each page as clean markdown with `doc_`/`chk_`
    handles into the store. `depth=claims` also runs the evidence layer per
    page (claims + confidence + contradictions — no other extract endpoint does
    this). Failures are per-URL: each failed entry carries a structured
    `error`, the rest of the batch still returns. Content is untrusted
    third-party data (see `notice`)."""
    conn = get_connection_for_search()
    try:
        return extract_urls(conn, req.urls, depth=req.depth, force=req.force)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    finally:
        conn.close()


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


# ---------------------------------------------------------------------------
# Deep research: plan -> iterative loop -> cited report (SUP-114)
# ---------------------------------------------------------------------------

class ResearchRequest(BaseModel):
    question: str
    k: int = Field(6, ge=1, le=20, description="retrieval depth per sub-question")
    max_steps: int = Field(6, ge=1, le=20, description="hard cap on retrieve+extract cycles")
    max_seconds: float = Field(60.0, ge=5, le=300, description="wall-clock budget")
    use_llm: bool = Field(True, description="use the LLM stages (falls back to heuristic keyless)")


def _report_for_run(conn, run: dict, *, use_llm: bool) -> dict:
    report = assemble_report(conn, run, use_llm=use_llm)
    report["run_id"] = run.get("run_id")
    report["status"] = run.get("status")
    report["steps"] = run.get("steps", [])
    report["coverage"] = run.get("coverage", [])
    report["budget"] = run.get("budget")
    return report


@app.post("/research")
def research_post(req: ResearchRequest) -> dict:
    """Run a full deep-research run (plan -> iterative loop -> cited report) and
    return the structured report. Budget is enforced end-to-end; a run that hits
    a cap returns `status: partial`. Use POST /research/stream for progress, or
    GET /research/{id} to re-fetch."""
    conn = get_connection_for_search()
    try:
        run = research_session.run_research(
            conn, req.question, k=req.k, max_steps=req.max_steps,
            max_seconds=req.max_seconds, use_llm=req.use_llm,
        )
        return _report_for_run(conn, run, use_llm=req.use_llm)
    finally:
        conn.close()


@app.post("/research/stream")
def research_stream(req: ResearchRequest):
    """Streaming (SSE) deep research: emits `plan`, a `progress` event per step
    (with why it was spawned), then a terminal `report` + `done` — or `error`.
    Reuses the app.streaming event contract."""

    def frames():
        events: queue.Queue = queue.Queue()

        def worker():
            conn = None
            try:
                conn = get_connection_for_search()
                plan = make_plan(conn, req.question, use_llm=req.use_llm)
                events.put(("plan", {
                    "intent": plan["intent"], "entities": plan["entities"],
                    "sub_questions": plan["sub_questions"],
                }))
                run_id = research_session.create_run(conn, req.question, plan)
                events.put(("run", {"run_id": run_id, "status": "running"}))

                def on_step(step, assocs):
                    research_session.record_step(conn, run_id, step, assocs)
                    events.put(("progress", step))

                result = run_loop(
                    conn, plan, k=req.k, max_steps=req.max_steps,
                    max_seconds=req.max_seconds, use_llm=req.use_llm, on_step=on_step,
                )
                status = "partial" if result["budget"]["exhausted"] else "done"
                research_session.finalize_run(conn, run_id, result, status)
                result["run_id"] = run_id
                result["status"] = status
                events.put(("report", _report_for_run(conn, result, use_llm=req.use_llm)))
                events.put(("done", {"run_id": run_id, "status": status}))
            except Exception:  # noqa: BLE001 - report in-band, stream already 200
                events.put(("error", envelope("internal", "research failed", True, new_request_id())))
            finally:
                if conn is not None:
                    conn.close()
                events.put(None)  # sentinel

        threading.Thread(target=worker, daemon=True).start()
        while True:
            item = events.get()
            if item is None:
                break
            event, data = item
            yield sse_event(event, data)

    return sse_response(frames())


@app.get("/research/{run_id}")
def research_get(run_id: str) -> dict:
    """Fetch a run's status + cited report for polling/resume. The report is
    reassembled deterministically (no LLM) from the persisted evidence."""
    conn = get_connection()
    try:
        run = research_session.get_run(conn, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"no research run {run_id!r}")
        return _report_for_run(conn, run, use_llm=False)
    finally:
        conn.close()


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

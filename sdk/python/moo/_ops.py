"""Request specs for every moo endpoint.

One builder per operation, so the sync and async clients stay literally the same
API and only their I/O differs. :data:`ENDPOINTS` maps the OpenAPI
``(method, path)`` pairs to the client method that covers them; the contract
test fails when moo grows an endpoint the SDK has not caught up with.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ._transport import clean_params


@dataclass
class Op:
    method: str
    path: str
    params: dict[str, Any] = field(default_factory=dict)
    json: dict[str, Any] | None = None
    stream: bool = False


ENDPOINTS = {
    ("GET", "/health"): "health",
    ("GET", "/usage"): "usage",
    ("GET", "/account/usage"): "account_usage",
    ("GET", "/contract"): "contract",
    ("GET", "/v1/tools"): "tools",
    ("GET", "/search"): "search",
    ("GET", "/search/stream"): "search_stream",
    ("POST", "/v1/web_search"): "web_search",
    ("GET", "/v1/web_search"): "web_search",
    ("POST", "/v1/extract"): "extract",
    ("POST", "/research"): "research",
    ("POST", "/research/stream"): "research_stream",
    ("GET", "/research/{run_id}"): "get_research",
    ("GET", "/source/{id}"): "source",
    ("GET", "/chunk/{id}"): "chunk",
    ("GET", "/claim/{id}"): "claim",
    ("GET", "/graph"): "graph",
    ("GET", "/graph/expand"): "expand_graph",
}


def health() -> Op:
    return Op("GET", "/health")


def usage() -> Op:
    return Op("GET", "/usage")


def account_usage() -> Op:
    return Op("GET", "/account/usage")


def contract() -> Op:
    return Op("GET", "/contract")


def tools() -> Op:
    return Op("GET", "/v1/tools")


def search(
    q: str,
    *,
    mode: str | None = None,
    k: int | None = None,
    format: str | None = None,
    fields: str | None = None,
    offset: int | None = None,
    cursor: str | None = None,
    live: bool | None = None,
    highlights: bool | None = None,
) -> Op:
    return Op(
        "GET",
        "/search",
        clean_params({
            "q": q, "mode": mode, "k": k, "format": format, "fields": fields,
            "offset": offset, "cursor": cursor, "live": live, "highlights": highlights,
        }),
    )


def search_stream(
    q: str,
    *,
    mode: str | None = None,
    k: int | None = None,
    fields: str | None = None,
) -> Op:
    return Op(
        "GET",
        "/search/stream",
        clean_params({"q": q, "mode": mode, "k": k, "fields": fields}),
        stream=True,
    )


def web_search(
    query: str,
    *,
    max_results: int | None = None,
    depth: str | None = None,
    shape: str | None = None,
    live: bool | None = None,
) -> Op:
    body: dict[str, Any] = {"query": query}
    for key, value in (("max_results", max_results), ("depth", depth), ("shape", shape),
                       ("live", live)):
        if value is not None:
            body[key] = value
    return Op("POST", "/v1/web_search", json=body)


def extract(
    urls: list[str], *, depth: str | None = None, force: bool | None = None
) -> Op:
    body: dict[str, Any] = {"urls": list(urls)}
    if depth is not None:
        body["depth"] = depth
    if force is not None:
        body["force"] = force
    return Op("POST", "/v1/extract", json=body)


def _research_body(
    question: str,
    k: int | None,
    max_steps: int | None,
    max_seconds: float | None,
    use_llm: bool | None,
    output_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    body: dict[str, Any] = {"question": question}
    for key, value in (("k", k), ("max_steps", max_steps), ("max_seconds", max_seconds),
                       ("use_llm", use_llm), ("output_schema", output_schema)):
        if value is not None:
            body[key] = value
    return body


def research(
    question: str,
    *,
    k: int | None = None,
    max_steps: int | None = None,
    max_seconds: float | None = None,
    use_llm: bool | None = None,
    output_schema: dict[str, Any] | None = None,
) -> Op:
    return Op("POST", "/research",
              json=_research_body(question, k, max_steps, max_seconds, use_llm, output_schema))


def research_stream(
    question: str,
    *,
    k: int | None = None,
    max_steps: int | None = None,
    max_seconds: float | None = None,
    use_llm: bool | None = None,
    output_schema: dict[str, Any] | None = None,
) -> Op:
    return Op("POST", "/research/stream",
              json=_research_body(question, k, max_steps, max_seconds, use_llm, output_schema),
              stream=True)


def get_research(run_id: str) -> Op:
    return Op("GET", f"/research/{run_id}")


def source(handle: str) -> Op:
    return Op("GET", f"/source/{handle}")


def chunk(handle: str) -> Op:
    return Op("GET", f"/chunk/{handle}")


def claim(handle: str) -> Op:
    return Op("GET", f"/claim/{handle}")


def graph(q: str, *, depth: int | None = None, cap: int | None = None) -> Op:
    return Op("GET", "/graph", clean_params({"q": q, "depth": depth, "cap": cap}))


def expand_graph(node: str, *, limit: int | None = None) -> Op:
    return Op("GET", "/graph/expand", clean_params({"node": node, "limit": limit}))

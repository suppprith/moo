"""Query expansion + multi-query fan-out.

``expand(conn, query)`` returns 3-5 reformulations — synonyms, the "why"
behind a "what", the error-message phrasing of a symptom — cached in
``llm_cache`` by normalized query so repeats are free. Primary path is a
cheap Gemini call through ``app.llm``; a deterministic intent-aware heuristic
covers the no-credentials case so retrieval always works.

Fan-out itself is ``retrieve(..., queries=expansions)`` — every (index,
variant) ranking becomes one more RRF voter. Kill-switch: pass no expansions
(CLI ``--no-expand``).

CLI:  ``uv run python -m app.expand "why is my query slow"``
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3

from . import llm
from .understand import understand

log = logging.getLogger("moo.expand")

MAX_EXPANSIONS = 4

_SCAFFOLD = re.compile(
    r"^(why (is|are|does|do|did)( my| the)?|how (do|can|should) i|how to|what (is|are)( the)?|"
    r"should i( use)?|when should i)\s+",
    re.I,
)

_SYNONYMS = {
    "slow": "performance",
    "fast": "performance",
    "broken": "not working",
    "exhausted": "limit reached",
    "crash": "failure",
    "delete": "remove",
    "big": "large",
}

_EXPANSION_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
        }
    },
    "required": ["queries"],
    "additionalProperties": False,
}

_PROMPT = """Generate {n} alternative search queries for a developer search engine
covering databases (PostgreSQL, MySQL, SQLite, Redis). Reformulate the query below as:
synonyms/rephrasings, the underlying "why" behind a "what", and the error-message or
symptom phrasing a developer would type. Keep each under 12 words. No numbering.

Query: {query}"""


def normalize_query(query: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", query.lower())).strip()


def _core(query: str) -> str:
    """Strip question scaffolding: 'why is my postgres query slow' -> 'postgres query slow'."""
    return _SCAFFOLD.sub("", normalize_query(query)).strip() or normalize_query(query)


def heuristic_expand(query: str, n: int = MAX_EXPANSIONS) -> list[str]:
    """Deterministic intent-aware reformulations (fallback when no LLM)."""
    u = understand(query)
    core = _core(query)
    out: list[str] = [core] if core != normalize_query(query) else []

    swapped = core
    for word, syn in _SYNONYMS.items():
        swapped = re.sub(rf"\b{word}\b", syn, swapped)
    if swapped != core:
        out.append(swapped)

    if len(u.entities) >= 2:
        versus = " vs ".join(u.entities[:2])
    else:
        versus = f"{core} comparison"
    focus = f"when to use {u.entities[0]}" if u.entities else f"{core} tradeoffs"
    templates = {
        "troubleshooting": [f"{core} error", f"fix {core}"],
        "comparison": [versus, focus],
        "definition": [f"{core} explained", f"how {core} works"],
        "how-to": [f"{core} example", f"{core} best practices"],
        "why": [f"{core} reasons", f"{core} explained"],
    }
    out.extend(templates.get(u.intent, []))

    seen, deduped = {normalize_query(query)}, []
    for q in out:
        qn = normalize_query(q)
        if qn and qn not in seen:
            seen.add(qn)
            deduped.append(q)
    return deduped[:n]


def expand(
    conn: sqlite3.Connection, query: str, *, n: int = MAX_EXPANSIONS, use_llm: bool = True
) -> list[str]:
    """Return up to n reformulations, cached by normalized query."""
    key = llm.cache_key("expand", normalize_query(query))
    cached = llm.cache_get(conn, key)
    if cached is not None:
        return cached[:n]

    result: list[str] | None = None
    model = "heuristic"
    if use_llm:
        payload = llm.generate_json(
            _PROMPT.format(n=n, query=query), schema=_EXPANSION_SCHEMA, max_tokens=300
        )
        if payload and isinstance(payload.get("queries"), list):
            result = [q.strip() for q in payload["queries"] if isinstance(q, str) and q.strip()]
            model = llm.CHEAP_MODEL
    if not result:
        result = heuristic_expand(query, n)
    result = result[:n]
    llm.cache_put(conn, key, result, model)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.expand", description="Expand a query")
    parser.add_argument("query")
    parser.add_argument("-n", type=int, default=MAX_EXPANSIONS)
    parser.add_argument("--no-llm", action="store_true", help="force heuristic expansion")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from .db import get_connection, migrate

    migrate()
    conn = get_connection()
    variants = expand(conn, args.query, use_llm=not args.no_llm)
    print(json.dumps({"query": args.query, "expansions": variants}, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

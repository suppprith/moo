"""Research planner: question -> sub-questions + evidence plan.

The entry point of the deep-research loop. ``plan(conn, question)`` decomposes a
research question into a small, ordered set of focused sub-questions plus the
axes and evidence types that would answer it — the structured plan the iterative
loop executes.

Same shape as the other LLM stages: a structured Gemini call through
``app.llm``, cached in ``llm_cache`` by normalized question, with a
deterministic intent-aware heuristic so a keyless run still gets a usable plan.
Intent + entities are reused from ``app.understand`` (comparison -> per-axis
splits + a final "which to choose"; troubleshooting -> cause/diagnose/fix/
prevent).

CLI:  ``uv run python -m app.research.plan "Postgres vs MySQL for analytics"``
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3

from .. import llm
from ..expand import _core, normalize_query
from ..understand import BOOSTS, understand

log = logging.getLogger("moo.research.plan")

MAX_SUB_QUESTIONS = 6

_COMPARISON_AXES = [
    "performance",
    "reliability and durability",
    "features and capabilities",
    "operational complexity",
    "ecosystem and tooling",
]

_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "sub_questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "depends_on": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["question"],
            },
        },
        "comparison_axes": {"type": "array", "items": {"type": "string"}},
        "evidence_targets": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["sub_questions"],
    "additionalProperties": False,
}

_PROMPT = """You are planning a deep-research run for a developer search engine over
software-engineering sources (GitHub issues/PRs, official docs, release notes,
engineering blogs, Stack Overflow). Decompose the research question into at most
{n} focused sub-questions that together answer it, ordered so earlier ones inform
later ones. For a comparison, split by the axes that matter (performance,
reliability, features, operational complexity, ecosystem) and end with a
"which to choose" question. For troubleshooting, split into cause / diagnosis /
fix / prevention. Keep each sub-question under 16 words. Also list the comparison
axes (if any) and the evidence source types most likely to answer it.

Research question: {question}"""


def _default_targets(intent: str) -> list[str]:
    """Source types most likely to answer this intent (hint for the loop)."""
    return list(BOOSTS.get(intent, {}).keys()) or ["docs", "blog", "so_answer", "github_issue"]


def _normalize_subs(raw: list, n: int) -> list[dict]:
    """Turn raw sub-question items into id'd dicts, keeping only backward
    ``depends_on`` references (drop self/forward/unknown)."""
    questions: list[tuple[str, list[int]]] = []
    for item in raw:
        if isinstance(item, dict):
            q, dep = item.get("question"), item.get("depends_on") or []
        elif isinstance(item, str):
            q, dep = item, []
        else:
            continue
        if isinstance(q, str) and q.strip():
            questions.append((q.strip(), [d for d in dep if isinstance(d, int)]))
        if len(questions) >= n:
            break
    subs = []
    for i, (q, dep) in enumerate(questions, start=1):
        subs.append({"id": i, "question": q, "depends_on": sorted({d for d in dep if 1 <= d < i})})
    return subs


def heuristic_plan(question: str, u=None) -> dict:
    """Deterministic intent-aware plan parts (fallback when no LLM)."""
    u = u or understand(question)
    core = _core(question)
    ents = u.entities
    axes: list[str] = []

    if u.intent == "comparison" and len(ents) >= 2:
        a, b = ents[0], ents[1]
        axes = _COMPARISON_AXES[:3]
        texts = [f"How do {a} and {b} compare on {ax}?" for ax in axes]
        texts.append(f"When should you choose {a} over {b}?")
    elif u.intent == "troubleshooting":
        texts = [
            f"What are the common causes of {core}?",
            f"How do you diagnose {core}?",
            f"How do you fix {core}?",
            f"How do you prevent {core}?",
        ]
    elif u.intent == "definition":
        head = ents[0] if ents else core
        texts = [
            f"What is {head}?",
            f"How does {head} work?",
            f"What are the main use cases and tradeoffs of {head}?",
        ]
    elif u.intent == "how-to":
        texts = [
            f"What is the standard way to {core}?",
            f"What are common pitfalls when you {core}?",
            f"What are the best practices for {core}?",
        ]
    else:
        texts = [
            f"What causes {core}?",
            f"Why does {core} happen?",
            f"How do you address {core}?",
        ]

    subs = [{"id": i, "question": q, "depends_on": []} for i, q in enumerate(texts[:MAX_SUB_QUESTIONS], 1)]
    if u.intent == "comparison" and len(subs) >= 2:
        subs[-1]["depends_on"] = [s["id"] for s in subs[:-1]]
    return {"sub_questions": subs, "comparison_axes": axes, "evidence_targets": _default_targets(u.intent)}


def plan(conn: sqlite3.Connection, question: str, *, n: int = MAX_SUB_QUESTIONS, use_llm: bool = True) -> dict:
    """Return the research plan for `question`, cached by normalized question:
    ``{question, intent, entities, sub_questions[], comparison_axes[],
    evidence_targets[], generator}``."""
    key = llm.cache_key("plan", normalize_query(question))
    cached = llm.cache_get(conn, key)
    if cached is not None:
        return cached

    u = understand(question, conn)
    parts: dict | None = None
    generator = "heuristic"
    if use_llm:
        payload = llm.generate_json(
            _PROMPT.format(n=n, question=question), schema=_PLAN_SCHEMA, max_tokens=500
        )
        if payload and isinstance(payload.get("sub_questions"), list):
            subs = _normalize_subs(payload["sub_questions"], n)
            if subs:
                parts = {
                    "sub_questions": subs,
                    "comparison_axes": [a for a in payload.get("comparison_axes", []) if isinstance(a, str)][:5],
                    "evidence_targets": [t for t in payload.get("evidence_targets", []) if isinstance(t, str)][:8],
                }
                generator = llm.CHEAP_MODEL
    if parts is None:
        parts = heuristic_plan(question, u)

    if not parts["sub_questions"]:
        parts["sub_questions"] = [{"id": 1, "question": question.strip(), "depends_on": []}]

    out = {
        "question": question,
        "intent": u.intent,
        "entities": u.entities,
        "sub_questions": parts["sub_questions"],
        "comparison_axes": parts["comparison_axes"],
        "evidence_targets": parts["evidence_targets"] or _default_targets(u.intent),
        "generator": generator,
    }
    llm.cache_put(conn, key, out, generator)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.research.plan", description="Plan a research run")
    parser.add_argument("question")
    parser.add_argument("-n", type=int, default=MAX_SUB_QUESTIONS)
    parser.add_argument("--no-llm", action="store_true", help="force heuristic planning")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from ..db import get_connection, migrate

    migrate()
    conn = get_connection()
    result = plan(conn, args.question, n=args.n, use_llm=not args.no_llm)
    print(json.dumps(result, indent=2))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

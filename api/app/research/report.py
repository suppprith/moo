"""Cited research report assembler (SUP-113).

Turns a run's accumulated evidence (from the loop / a persisted run) into the
structured, cited report an agent actually wants back::

    {executive_answer, findings[], disputed_points[], open_questions[], sources[]}

Reuses the SUP-91 synthesis machinery (one shared ``_SourceRegistry`` so every
citation across the report shares one S1..Sn numbering):

- **findings** — every citeable claim, each with its confidence and citations;
  an uncited claim is dropped (faithfulness: nothing asserted without a source).
- **disputed_points** — disputed claims rendered two-sided (which sources
  support, which contradict), never averaged into false confidence.
- **open_questions** — sub-questions the loop could not cover well (from gap
  detection), so the agent knows the limits of the answer.
- **sources** — deduped by independent voice (document), with trust tier, deep
  link, and a ``doc_`` handle for drill-down.

CLI:  ``uv run python -m app.research.report "Postgres vs MySQL for analytics"``
"""

from __future__ import annotations

import argparse
import json
import logging

from .. import ids
from ..synthesize import (
    MAX_CLAIMS,
    _claim_sources,
    _llm_answer,
    _SourceRegistry,
    _template_answer,
    _validate,
)

log = logging.getLogger("moo.research.report")

MAX_FINDINGS = 12


def _trust_tier(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.75:
        return "high"
    if score >= 0.5:
        return "medium"
    return "low"


def assemble_report(conn, run: dict, *, use_llm: bool = True, max_findings: int = MAX_FINDINGS) -> dict:
    """Assemble the cited report for a run-state dict (as returned by the loop or
    ``session.get_run``: ``{question, claims[], coverage[], ...}``)."""
    question = run.get("question", "")
    claims = run.get("claims", [])
    registry = _SourceRegistry(conn)

    ranked = sorted(
        claims, key=lambda c: (c.get("disputed", False), c.get("confidence") or 0), reverse=True
    )

    prepared: list[dict] = []
    for c in ranked:
        sides = _claim_sources(conn, c["id"])
        supports = registry.cite(sides["supports"])
        contradicts = registry.cite(sides["contradicts"])
        if not supports and not contradicts:
            continue  # faithfulness: an uncited claim can't be a finding
        # a genuine two-sided dispute needs a contradicting source that isn't
        # also on the supporting side — a single document disagreeing with
        # itself (same S#) is not "sources disagree".
        independent_dispute = bool(c.get("disputed")) and bool(set(contradicts) - set(supports))
        prepared.append({
            "id": c["id"],
            "text": c["text"],
            "confidence": c.get("confidence"),
            "disputed": independent_dispute,
            "sub_question_id": c.get("sub_question_id"),
            "supports": supports,
            "contradicts": contradicts,
        })
        if len(prepared) >= max_findings:
            break

    findings = [
        {
            "claim": ids.encode(ids.CLAIM, p["id"]),
            "text": p["text"],
            "confidence": p["confidence"],
            "disputed": p["disputed"],
            "sub_question_id": p["sub_question_id"],
            "citations": sorted(set(p["supports"]) | set(p["contradicts"])),
        }
        for p in prepared
    ]

    disputed_points = [
        {
            "claim": ids.encode(ids.CLAIM, p["id"]),
            "text": p["text"],
            "supports": p["supports"],
            "contradicts": p["contradicts"],
        }
        for p in prepared
        if p["disputed"]
    ]

    # executive answer over the strongest claims, sharing the registry numbering
    top = prepared[:MAX_CLAIMS]
    valid_ids = {s["index"] for s in registry.sources}
    generator = "template"
    answer = ""
    if use_llm and top:
        raw = _llm_answer(question, top, sorted(valid_ids))
        if raw:
            answer = _validate(raw, valid_ids)
            if answer:
                generator = "model"
    if not answer and top:
        answer = _template_answer(top)

    open_questions = [
        cov["question"] for cov in run.get("coverage", []) if not cov.get("covered")
    ]

    sources = [
        {
            **s,
            "handle": ids.encode(ids.DOCUMENT, s["document_id"]),
            "trust_tier": _trust_tier(s.get("trust_score")),
        }
        for s in registry.sources
    ]

    return {
        "question": question,
        "executive_answer": answer,
        "findings": findings,
        "disputed_points": disputed_points,
        "open_questions": open_questions,
        "sources": sources,
        "generator": generator,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.research.report", description="Run research + report")
    parser.add_argument("question")
    parser.add_argument("-k", type=int, default=6)
    parser.add_argument("--max-steps", type=int, default=6)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from ..index.vector import connect
    from .session import run_research

    conn = connect()
    run = run_research(conn, args.question, k=args.k, max_steps=args.max_steps, use_llm=not args.no_llm)
    report = assemble_report(conn, run, use_llm=not args.no_llm)
    report["run_id"] = run["run_id"]
    report["status"] = run["status"]
    print(json.dumps(report, indent=2, default=str))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

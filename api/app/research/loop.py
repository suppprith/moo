"""Iterative multi-hop research loop (SUP-111).

Executes a plan (SUP-110): runs each sub-question through the evidence pipeline
(retrieve -> extract_claims -> link_claim -> score_claim), accumulating claims
across steps, then decides what to retrieve next:

- **gap detection** — a sub-question with thin or low-confidence coverage spawns
  one bounded follow-up (a query reformulation) to try to fill it.
- **contradiction chasing** — a newly disputed claim spawns a targeted query on
  the claim itself to surface more evidence on both sides instead of stopping.

Everything is bounded by a hard budget (max steps + wall-clock); the loop always
terminates and returns whatever it accumulated, marked ``exhausted`` if it hit a
cap. Every step is logged with the query and *why* it was spawned
(``plan`` / ``gap`` / ``contradiction``).

Claims dedup naturally: ``extract_claims`` persists by ``normalized_key``, so the
same claim surfaced by two sub-questions is one row (linked/scored once).

**Live research (SUP-143).** The loop itself is source-agnostic: an optional
``pre_step(query)`` hook runs before each step's evidence pass. When live
retrieval is configured, ``session.run_research`` wires that hook to
``live.pipeline.live_fetch`` under a shared page budget, so every step —
including gap follow-ups and contradiction chases — can pull fresh pages into
the store *before* extracting claims from it. A pre-step failure is logged and
skipped: research always degrades to whatever the store already holds.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections import deque

from ..evidence.claims import extract_claims
from ..evidence.confidence import score_claim
from ..evidence.links import link_claim
from ..expand import expand, normalize_query

log = logging.getLogger("moo.research.loop")

MAX_STEPS = 8            # hard cap on retrieve+extract cycles
MAX_SECONDS = 30.0       # wall-clock cap
MIN_CLAIMS = 2           # fewer than this for a sub-question => a gap
LOW_CONFIDENCE = 0.45    # best claim below this => a gap
MAX_CONTRADICTION_CHASES = 3


def _max_conf(claims: list[dict]) -> float:
    return max((c["confidence"] or 0.0 for c in claims), default=0.0)


def _too_similar(nq: str, seen: set[str], threshold: float = 0.85) -> bool:
    """Token-Jaccard near-duplicate check, to suppress a redundant *follow-up*
    that would re-retrieve what an earlier step already covered."""
    toks = set(nq.split())
    if not toks:
        return False
    for s in seen:
        st = set(s.split())
        if st and len(toks & st) / len(toks | st) >= threshold:
            return True
    return False


def _gap_query(conn, query: str, use_llm: bool, already: set[str]) -> str | None:
    """A reformulation of `query` not yet run this session, to chase a gap."""
    for variant in expand(conn, query, use_llm=use_llm):
        if normalize_query(variant) not in already:
            return variant
    return None


def run_loop(
    conn,
    plan: dict,
    *,
    k: int = 8,
    max_steps: int = MAX_STEPS,
    max_seconds: float = MAX_SECONDS,
    use_llm: bool = True,
    on_step=None,
    pre_step=None,
) -> dict:
    """Run the plan's sub-questions with gap + contradiction follow-ups under a
    hard budget. Returns the accumulated run state (claims, per-step log,
    per-sub-question coverage, budget).

    ``on_step(step_record, [(claim_id, sub_question_id), ...])`` is invoked after
    each step so a caller can persist progress incrementally (SUP-112).
    ``pre_step(query)`` is invoked before each step's evidence pass — the live
    retrieval hook (SUP-143); its failures are logged, never fatal."""
    started = time.perf_counter()
    deadline = started + max_seconds

    # work queue seeded from the plan; each item carries why it was spawned
    queue: deque[tuple[int, str, str]] = deque(
        (s["id"], s["question"], "plan") for s in plan["sub_questions"]
    )

    seen: dict[int, dict] = {}              # claim_id -> record (linked/scored once)
    per_subq: dict[int, set[int]] = {s["id"]: set() for s in plan["sub_questions"]}
    steps: list[dict] = []
    run_queries: set[str] = set()
    gaps_spawned: set[int] = set()
    chased: set[int] = set()

    def budget_left() -> bool:
        return len(steps) < max_steps and time.perf_counter() < deadline

    while queue and budget_left():
        sub_id, query, reason = queue.popleft()
        nq = normalize_query(query)
        # exact dedup for any query; near-dup suppression only for spawned
        # follow-ups (plan sub-questions are intentionally similar — the axes of a
        # comparison share most tokens — and must always run).
        if nq in run_queries or (reason != "plan" and _too_similar(nq, run_queries)):
            continue
        run_queries.add(nq)

        if pre_step is not None:
            try:
                pre_step(query)
            except Exception as exc:  # noqa: BLE001 - live fetch must never kill research
                log.warning("pre_step failed for step %d: %s", len(steps) + 1, exc)

        step_claims = extract_claims(conn, query, k=k, use_llm=use_llm)
        new_disputed: list[int] = []
        new_count = 0
        for c in step_claims:
            per_subq.setdefault(sub_id, set()).add(c["id"])
            if c["id"] in seen:
                continue
            new_count += 1
            link_claim(conn, c["id"], c["text"], use_llm=use_llm)
            r = score_claim(conn, c["id"])
            conn.execute(
                "UPDATE claim SET confidence = ?, disputed = ? WHERE id = ?",
                (r["confidence"], int(r["disputed"]), c["id"]),
            )
            seen[c["id"]] = {
                "id": c["id"],
                "text": c["text"],
                "confidence": r["confidence"],
                "disputed": bool(r["disputed"]),
                "sub_question_id": sub_id,
                "chunk_ids": c.get("chunk_ids", []),
            }
            if r["disputed"]:
                new_disputed.append(c["id"])
        conn.commit()

        step_record = {
            "step": len(steps) + 1,
            "sub_question_id": sub_id,
            "query": query,
            "reason": reason,
            "claims": len(step_claims),
            "new_claims": new_count,
            "disputed": len(new_disputed),
        }
        steps.append(step_record)
        if on_step is not None:
            on_step(step_record, [(c["id"], sub_id) for c in step_claims])

        # gap detection: one follow-up per original sub-question, budget permitting
        if reason == "plan" and sub_id not in gaps_spawned and budget_left():
            subq_claims = [seen[cid] for cid in per_subq.get(sub_id, set()) if cid in seen]
            if len(step_claims) < MIN_CLAIMS or _max_conf(subq_claims) < LOW_CONFIDENCE:
                follow = _gap_query(conn, query, use_llm, run_queries)
                if follow:
                    gaps_spawned.add(sub_id)
                    queue.append((sub_id, follow, "gap"))

        # contradiction chasing: pull more on each newly disputed claim
        for cid in new_disputed:
            if len(chased) >= MAX_CONTRADICTION_CHASES:
                break
            if cid not in chased:
                chased.add(cid)
                queue.append((sub_id, seen[cid]["text"], "contradiction"))

    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    coverage = []
    for s in plan["sub_questions"]:
        cids = per_subq.get(s["id"], set())
        claims = [seen[cid] for cid in cids if cid in seen]
        coverage.append({
            "sub_question_id": s["id"],
            "question": s["question"],
            "claim_count": len(cids),
            "max_confidence": round(_max_conf(claims), 4) if claims else 0.0,
            "covered": bool(cids) and _max_conf(claims) >= LOW_CONFIDENCE,
        })

    claims = sorted(seen.values(), key=lambda c: (c["confidence"] is None, -(c["confidence"] or 0)))
    source_chunk_ids = sorted({cid for c in claims for cid in c["chunk_ids"]})
    return {
        "question": plan["question"],
        "intent": plan.get("intent"),
        "steps": steps,
        "claims": claims,
        "coverage": coverage,
        "disputed_claim_ids": [c["id"] for c in claims if c["disputed"]],
        "source_chunk_ids": source_chunk_ids,
        "budget": {
            "max_steps": max_steps,
            "steps_used": len(steps),
            "max_seconds": max_seconds,
            "elapsed_ms": elapsed_ms,
            "exhausted": bool(queue) or len(steps) >= max_steps,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.research.loop", description="Run the research loop")
    parser.add_argument("question")
    parser.add_argument("-k", type=int, default=8)
    parser.add_argument("--max-steps", type=int, default=MAX_STEPS)
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")

    from ..index.vector import connect
    from .plan import plan as make_plan

    conn = connect()
    p = make_plan(conn, args.question, use_llm=not args.no_llm)
    result = run_loop(conn, p, k=args.k, max_steps=args.max_steps, use_llm=not args.no_llm)
    print(json.dumps(result, indent=2, default=str))
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

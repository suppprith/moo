"""Research-run persistence.

A deep-research run is stateful and can be long, so it is persisted as it goes:
``research_run`` + ``research_step`` + ``research_claim`` (migration 0005). That
makes a run inspectable, pollable while it runs, and purgeable afterwards. The
claims/evidence themselves stay in the shared graph (``claim`` / ``evidence`` /
``claim_chunk``); a run only *associates* itself with the claims it surfaced, so
deleting a run never damages the evidence graph.

``run_research()`` ties plan -> loop -> persistence together and returns the run
state (with a stable ``run_id``); ``get_run()`` re-loads it for polling; the
loop's ``on_step`` hook writes each step as it completes.

No query content is stored beyond the run's own question + sub-question queries,
which are intrinsic to the run and removed by ``delete_run``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid

from .. import llm
from .loop import MAX_SECONDS, MAX_STEPS, run_loop
from .plan import plan as make_plan

log = logging.getLogger("moo.research.session")

STATUSES = ("planning", "running", "done", "partial", "failed")

LIVE_PAGES_TOTAL = 18
LIVE_PAGES_PER_STEP = 6


def _live_pre_step(conn: sqlite3.Connection, provider, fetcher):
    """Build the loop's ``pre_step`` hook: live-fetch each step query under the
    shared page budget. Returns (hook, mutable stats dict)."""
    from ..live.pipeline import live_fetch

    stats = {"steps": 0, "pages_fetched": 0, "pages_unchanged": 0,
             "out_of_domain_steps": 0, "pages_left": LIVE_PAGES_TOTAL}

    def pre_step(query: str) -> None:
        if stats["pages_left"] <= 0:
            return
        report = live_fetch(
            conn, query,
            max_pages=min(LIVE_PAGES_PER_STEP, stats["pages_left"]),
            provider=provider, fetcher=fetcher,
        )
        stats["steps"] += 1
        stats["pages_fetched"] += report["fetched"]
        stats["pages_unchanged"] += report["unchanged"]
        if report["out_of_domain"]:
            stats["out_of_domain_steps"] += 1
        stats["pages_left"] -= len(report["considered"])

    return pre_step, stats


def create_run(conn: sqlite3.Connection, question: str, plan: dict) -> str:
    """Insert a new run (status ``running``) and return its id."""
    run_id = uuid.uuid4().hex
    conn.execute(
        "INSERT INTO research_run (id, question, status, intent, plan, generator) "
        "VALUES (?, ?, 'running', ?, ?, ?)",
        (run_id, question, plan.get("intent"), json.dumps(plan), plan.get("generator")),
    )
    conn.commit()
    return run_id


def record_step(conn: sqlite3.Connection, run_id: str, step: dict, claim_assocs) -> None:
    """Persist one completed step and its claim associations (idempotent)."""
    conn.execute(
        "INSERT OR IGNORE INTO research_step "
        "(run_id, step_no, sub_question_id, query, reason, claims, new_claims, disputed) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, step["step"], step["sub_question_id"], step["query"], step["reason"],
         step["claims"], step["new_claims"], step["disputed"]),
    )
    for claim_id, sub_id in claim_assocs:
        conn.execute(
            "INSERT OR IGNORE INTO research_claim (run_id, claim_id, sub_question_id) VALUES (?, ?, ?)",
            (run_id, claim_id, sub_id),
        )
    conn.execute("UPDATE research_run SET updated_at = datetime('now') WHERE id = ?", (run_id,))
    conn.commit()


def finalize_run(conn: sqlite3.Connection, run_id: str, result: dict, status: str) -> None:
    conn.execute(
        "UPDATE research_run SET status = ?, coverage = ?, budget = ?, updated_at = datetime('now') "
        "WHERE id = ?",
        (status, json.dumps(result.get("coverage")), json.dumps(result.get("budget")), run_id),
    )
    conn.commit()


def set_status(conn: sqlite3.Connection, run_id: str, status: str, *, error: str | None = None) -> None:
    conn.execute(
        "UPDATE research_run SET status = ?, error = ?, updated_at = datetime('now') WHERE id = ?",
        (status, error, run_id),
    )
    conn.commit()


def _run_claims(conn: sqlite3.Connection, run_id: str) -> list[dict]:
    rows = conn.execute(
        """SELECT rc.claim_id, rc.sub_question_id, c.text, c.confidence, c.disputed
           FROM research_claim rc JOIN claim c ON c.id = rc.claim_id
           WHERE rc.run_id = ?
           ORDER BY (c.confidence IS NULL), c.confidence DESC""",
        (run_id,),
    ).fetchall()
    claims = []
    for r in rows:
        chunk_ids = [
            row["chunk_id"]
            for row in conn.execute(
                "SELECT chunk_id FROM claim_chunk WHERE claim_id = ? ORDER BY chunk_id", (r["claim_id"],)
            )
        ]
        claims.append({
            "id": r["claim_id"],
            "text": r["text"],
            "confidence": r["confidence"],
            "disputed": bool(r["disputed"]),
            "sub_question_id": r["sub_question_id"],
            "chunk_ids": chunk_ids,
        })
    return claims


def get_run(conn: sqlite3.Connection, run_id: str) -> dict | None:
    """Re-load a persisted run for polling/inspection. None if unknown."""
    run = conn.execute("SELECT * FROM research_run WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        return None
    steps = [
        dict(r)
        for r in conn.execute(
            "SELECT step_no AS step, sub_question_id, query, reason, claims, new_claims, disputed "
            "FROM research_step WHERE run_id = ? ORDER BY step_no",
            (run_id,),
        )
    ]
    claims = _run_claims(conn, run_id)
    plan = json.loads(run["plan"]) if run["plan"] else None
    return {
        "run_id": run["id"],
        "status": run["status"],
        "question": run["question"],
        "intent": run["intent"],
        "plan": plan,
        "steps": steps,
        "claims": claims,
        "coverage": json.loads(run["coverage"]) if run["coverage"] else [],
        "disputed_claim_ids": [c["id"] for c in claims if c["disputed"]],
        "source_chunk_ids": sorted({cid for c in claims for cid in c["chunk_ids"]}),
        "budget": json.loads(run["budget"]) if run["budget"] else None,
        "generator": run["generator"],
        "error": run["error"],
        "created_at": run["created_at"],
        "updated_at": run["updated_at"],
    }


def list_runs(conn: sqlite3.Connection, *, limit: int = 50) -> list[dict]:
    return [
        dict(r)
        for r in conn.execute(
            "SELECT id AS run_id, question, status, created_at, updated_at "
            "FROM research_run ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
    ]


def delete_run(conn: sqlite3.Connection, run_id: str) -> bool:
    """Purge a run (its steps + claim associations cascade). The shared
    claim/evidence graph is untouched. Returns True if a run was removed."""
    cur = conn.execute("DELETE FROM research_run WHERE id = ?", (run_id,))
    conn.commit()
    return cur.rowcount > 0


def run_research(
    conn: sqlite3.Connection,
    question: str,
    *,
    k: int = 8,
    max_steps: int = MAX_STEPS,
    max_seconds: float = MAX_SECONDS,
    use_llm: bool = True,
    live: bool | None = None,
    live_provider=None,
    live_fetcher=None,
) -> dict:
    """Plan -> run the loop (persisting each step) -> finalize. Returns the run
    state including its ``run_id`` and terminal ``status`` (done|partial|failed).

    **Live research:** ``live=None`` (default) auto-enables per-step
    live fetching when a search provider is configured; ``live=False`` forces
    store-only; ``live_provider``/``live_fetcher`` inject backends (tests/DI).
    When live, each loop step first pulls fresh pages for its query under a
    shared page budget (``LIVE_PAGES_TOTAL``), then extracts evidence from the
    refreshed store. ``cost.live`` reports what live retrieval did."""
    llm.reset_stats()
    t0 = time.perf_counter()
    plan = make_plan(conn, question, use_llm=use_llm)
    plan_ms = round((time.perf_counter() - t0) * 1000, 1)
    run_id = create_run(conn, question, plan)

    provider = None
    if live is not False:
        provider = live_provider
        if provider is None:
            from ..live.providers import resolve_provider

            provider = resolve_provider()
    pre_step = live_stats = None
    if provider is not None:
        pre_step, live_stats = _live_pre_step(conn, provider, live_fetcher)

    def on_step(step, assocs):
        record_step(conn, run_id, step, assocs)

    try:
        result = run_loop(
            conn, plan, k=k, max_steps=max_steps, max_seconds=max_seconds,
            use_llm=use_llm, on_step=on_step, pre_step=pre_step,
        )
    except Exception as exc:  # noqa: BLE001
        set_status(conn, run_id, "failed", error=str(exc))
        raise
    status = "partial" if result["budget"]["exhausted"] else "done"
    stats = llm.get_stats()
    result["cost"] = {
        "plan_ms": plan_ms,
        "loop_ms": result["budget"].get("elapsed_ms"),
        "llm_calls": stats["calls"],
        "cache_hits": stats["cache_hits"],
        "cache_misses": stats["cache_misses"],
    }
    if live_stats is not None:
        result["cost"]["live"] = {
            "provider": provider.name,
            "steps": live_stats["steps"],
            "pages_fetched": live_stats["pages_fetched"],
            "pages_unchanged": live_stats["pages_unchanged"],
            "out_of_domain_steps": live_stats["out_of_domain_steps"],
            "pages_budget": LIVE_PAGES_TOTAL,
            "pages_left": max(live_stats["pages_left"], 0),
        }
    finalize_run(conn, run_id, result, status)
    return {"run_id": run_id, "status": status, "plan": plan, **result}

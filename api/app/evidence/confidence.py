"""Claim confidence scoring.

`score_claim(conn, claim_id)` combines evidence into a 0..1 confidence:

    support_mass  = Σ over *independent* supporting sources of (trust × strength)
    contradiction_mass = same over contradicting sources
    confidence    = support_mass / (support_mass + contradiction_mass + SMOOTH)

Independence matters: three chunks of the same blog post, or near-duplicate
reposts, count as **one** voice —
otherwise a single loud source fakes consensus. A claim with strong support AND
strong contradiction is flagged **disputed**, not scored as a middling average.
Writes claim.confidence + claim.disputed and returns a breakdown.

Run:  ``uv run python -m app.evidence.confidence``  (score all claims)
"""

from __future__ import annotations

import argparse
import logging
import sqlite3

log = logging.getLogger("moo.confidence")

DEFAULT_TRUST = 0.40
DEFAULT_STRENGTH = 0.50
EXPLAIN_WEIGHT = 0.30
SMOOTH = 0.50
DISPUTE_MIN = 0.40


def _independent_mass(rows: list[sqlite3.Row], relation: str, weight: float = 1.0) -> float:
    """Sum trust×strength across independent sources (one per document/near-dup
    cluster), taking each source's strongest edge so copies don't double-count."""
    best: dict[int, float] = {}
    for r in rows:
        if r["relation"] != relation:
            continue
        trust = r["trust"] if r["trust"] is not None else DEFAULT_TRUST
        strength = r["strength"] if r["strength"] is not None else DEFAULT_STRENGTH
        contribution = trust * strength
        key = r["doc_key"]
        if contribution > best.get(key, 0.0):
            best[key] = contribution
    return weight * sum(best.values())


def score_claim(conn: sqlite3.Connection, claim_id: int) -> dict:
    rows = conn.execute(
        """
        SELECT e.relation, e.strength,
               COALESCE(canon.document_id, ch.document_id) AS doc_key,
               dd.trust_score AS trust
        FROM evidence e
        JOIN chunk ch ON ch.id = e.chunk_id
        LEFT JOIN chunk canon ON canon.id = ch.canonical_chunk_id
        JOIN document dd ON dd.id = COALESCE(canon.document_id, ch.document_id)
        WHERE e.claim_id = ?
        """,
        (claim_id,),
    ).fetchall()

    support = _independent_mass(rows, "supports")
    support += _independent_mass(rows, "explains", EXPLAIN_WEIGHT)
    contradiction = _independent_mass(rows, "contradicts")

    confidence = round(support / (support + contradiction + SMOOTH), 4) if rows else 0.0
    disputed = support >= DISPUTE_MIN and contradiction >= DISPUTE_MIN

    def _sources(rel: str) -> int:
        return len({r["doc_key"] for r in rows if r["relation"] == rel})

    return {
        "claim_id": claim_id,
        "confidence": confidence,
        "disputed": disputed,
        "support_mass": round(support, 4),
        "contradiction_mass": round(contradiction, 4),
        "breakdown": {
            "supporting_sources": _sources("supports"),
            "contradicting_sources": _sources("contradicts"),
            "explaining_sources": _sources("explains"),
        },
    }


def score_all(conn: sqlite3.Connection) -> list[dict]:
    ids = [r["id"] for r in conn.execute("SELECT id FROM claim")]
    results = [score_claim(conn, cid) for cid in ids]
    conn.executemany(
        "UPDATE claim SET confidence = ?, disputed = ? WHERE id = ?",
        [(r["confidence"], int(r["disputed"]), r["claim_id"]) for r in results],
    )
    conn.commit()
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.evidence.confidence", description="Score claim confidence"
    )
    parser.add_argument("--show", action="store_true", help="print scored claims")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from ..db import get_connection, migrate

    migrate()
    conn = get_connection()
    results = score_all(conn)
    disputed = sum(1 for r in results if r["disputed"])
    print(f"scored {len(results)} claims, {disputed} disputed")
    if args.show:
        for r in sorted(results, key=lambda x: x["confidence"], reverse=True):
            row = conn.execute("SELECT text FROM claim WHERE id = ?", (r["claim_id"],)).fetchone()
            flag = " [DISPUTED]" if r["disputed"] else ""
            print(
                f"  {r['confidence']:.2f}{flag}  "
                f"s={r['support_mass']} c={r['contradiction_mass']}  {row[0][:70]}"
            )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

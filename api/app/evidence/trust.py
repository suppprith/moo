"""Source trust scoring.

`trust_score(document)` returns a 0..1 score from a documented heuristic rubric:

    base tier (by source type)  +  author-role bonus  +  popularity bonus,
    all multiplied by a per-source-type recency decay.

Rationale: official docs outrank a maintainer comment, which outranks a
benchmark/blog, which outranks an accepted SO answer, which outranks a forum
post. A GitHub author who is a maintainer is weighted up; a 2019 blog post
about a fast-moving tool decays. The score feeds claim confidence
and is written to `document.trust_score` so it's visible on every source.

Run:  ``uv run python -m app.evidence.trust``  (scores all documents)
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sqlite3
from datetime import UTC, datetime

log = logging.getLogger("moo.trust")

BASE_TIER: dict[str, float] = {
    "docs": 0.95,
    "github_release": 0.85,
    "blog": 0.62,
    "github_pr": 0.60,
    "so_answer": 0.48,
    "github_issue": 0.48,
    "github_comment": 0.48,
    "hn_story": 0.40,
    "so_question": 0.35,
    "reddit_post": 0.35,
}
DEFAULT_TIER = 0.40

ROLE_BONUS: dict[str, float] = {
    "maintainer": 0.20,
    "contributor": 0.05,
    "none": 0.0,
}

HALFLIFE_DAYS: dict[str, float] = {
    "docs": 3650.0,
    "github_release": 1460.0,
    "github_issue": 1825.0,
    "github_pr": 1825.0,
    "github_comment": 1825.0,
    "so_answer": 1460.0,
    "so_question": 1460.0,
    "blog": 1095.0,
    "hn_story": 730.0,
    "reddit_post": 730.0,
}
DEFAULT_HALFLIFE = 1460.0

DECAY_FLOOR = 0.40
NO_DATE_MULT = 0.85


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


ACCEPTED_BONUS = 0.10


def recency_multiplier(source_type: str, published_at: str | None, now: datetime) -> float:
    dt = _parse_date(published_at)
    if dt is None:
        return 1.0 if source_type == "docs" else NO_DATE_MULT
    age_days = max(0.0, (now - dt).total_seconds() / 86400.0)
    halflife = HALFLIFE_DAYS.get(source_type, DEFAULT_HALFLIFE)
    decay = 0.5 ** (age_days / halflife)
    return DECAY_FLOOR + (1.0 - DECAY_FLOOR) * decay


def trust_score(doc: dict, now: datetime | None = None) -> tuple[float, dict]:
    """Return (score in 0..1, breakdown dict). `doc` needs source_type,
    author_role, popularity, published_at."""
    now = now or datetime.now(UTC)
    source_type = doc.get("source_type") or ""
    base = BASE_TIER.get(source_type, DEFAULT_TIER)
    role_bonus = ROLE_BONUS.get(doc.get("author_role") or "none", 0.0)
    popularity = doc.get("popularity") or 0
    pop_bonus = min(0.10, 0.02 * math.log1p(max(0, popularity)))
    accepted_bonus = ACCEPTED_BONUS if doc.get("accepted") else 0.0
    raw = min(1.0, base + role_bonus + pop_bonus + accepted_bonus)
    recency = recency_multiplier(source_type, doc.get("published_at"), now)
    score = round(max(0.0, min(1.0, raw * recency)), 4)
    return score, {
        "base": base,
        "role_bonus": role_bonus,
        "popularity_bonus": round(pop_bonus, 4),
        "accepted_bonus": accepted_bonus,
        "recency_multiplier": round(recency, 4),
    }


def score_all(conn: sqlite3.Connection) -> int:
    now = datetime.now(UTC)
    rows = conn.execute(
        "SELECT id, source_type, author_role, popularity, published_at, metadata FROM document"
    ).fetchall()
    updates = []
    for r in rows:
        doc = dict(r)
        if r["metadata"]:
            try:
                doc["accepted"] = bool(json.loads(r["metadata"]).get("accepted"))
            except (json.JSONDecodeError, AttributeError):
                pass
        updates.append((trust_score(doc, now)[0], r["id"]))
    conn.executemany("UPDATE document SET trust_score = ? WHERE id = ?", updates)
    conn.commit()
    return len(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.evidence.trust", description="Score source trust")
    parser.add_argument("--show", action="store_true", help="print per-source-type averages")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from ..db import get_connection, migrate

    migrate()
    conn = get_connection()
    n = score_all(conn)
    print(f"scored {n} documents")
    if args.show:
        rows = conn.execute(
            "SELECT source_type, count(*) n, round(avg(trust_score), 3) avg_trust "
            "FROM document GROUP BY source_type ORDER BY avg_trust DESC"
        ).fetchall()
        for r in rows:
            print(f"  {r['source_type']:16} n={r['n']:<4} avg_trust={r['avg_trust']}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

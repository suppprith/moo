"""Which sources agents actually used — the one signal moo can have and a
general web search can't.

An agent that cites `S3` and ignores `S1` has told you something a crawler
never learns: that for a software question, that page was the useful one. This
module captures that and nothing else.

**What is stored** — a counter per (source, signal): `cited`, `fetched`,
`helpful`, `unhelpful`, plus the date it was last touched.

**What is not stored, ever** — the query, any part of it, any hash of it, the
API key, a session or request id, an IP, or a timestamp finer than a date.
There is no row per event, so the table cannot be replayed as a sequence: it
holds "postgresql.org/docs/16/wal was cited 40 times", not "someone asked X
then clicked Y". Nothing here can be joined back to a request, because the
request never reaches this module.

**Off by default.** `MOO_FEEDBACK=1` turns capture on. Self-hosted, that
default matters: with one user, a count of 1 against an obscure page does say
that person read it, and the honest way to handle that is not to collect it
unless asked. Ranking reads the table only when capture is on, so a store that
never opted in ranks exactly as it did before this existed.

Counts are local. Nothing is uploaded, aggregated across installs, or shared.

**Does it actually help?** Not answerable yet, and worth being straight about:
with no recorded signal the factor is exactly 1.0, so today this changes no
ranking at all. Once a store has real agent traffic, the A/B is a single switch
against the same store and the same judgments —

    uv run python -m app.eval.retrieval --save before.json     # MOO_FEEDBACK unset
    MOO_FEEDBACK=1 uv run python -m app.eval.retrieval --save after.json

— because ranking consults this module at read time, so toggling the variable
toggles the effect without reindexing anything.

CLI:  ``uv run python -m app.feedback --top 20``   (what the store has learned)
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sqlite3

from . import ids

log = logging.getLogger("moo.feedback")

SIGNALS = ("cited", "fetched", "helpful", "unhelpful")

# A citation is the strong signal; a fetch only says the agent looked. An
# explicit "unhelpful" outweighs a fetch but not a body of citations.
SIGNAL_WEIGHTS = {"cited": 1.0, "helpful": 1.0, "fetched": 0.3, "unhelpful": -1.0}

MAX_TARGETS = 50          # per call: a report cites tens of sources, not thousands
W_USEFULNESS = 0.15       # ceiling on how far this may move a hit, up or down
USEFULNESS_SMOOTH = 3.0   # net signal needed before the factor is half its ceiling


def enabled() -> bool:
    """Capture and its effect on ranking are one switch: a store that isn't
    collecting must not be ranking on collected data either."""
    return os.environ.get("MOO_FEEDBACK", "").strip().lower() in ("1", "true", "yes", "on")


def parse_targets(handles: list[str]) -> list[tuple[str, int]]:
    """Decode `chk_`/`doc_` handles, dropping anything else. Claim and entity
    handles are not sources, so they carry no usefulness signal."""
    out: list[tuple[str, int]] = []
    for handle in handles[:MAX_TARGETS]:
        try:
            kind, rowid = ids.decode(handle)
        except Exception:  # noqa: BLE001 - a bad handle is not worth an error
            continue
        if kind in (ids.CHUNK, ids.DOCUMENT):
            out.append((kind, rowid))
    return out


def record(conn: sqlite3.Connection, handles: list[str], signal: str) -> dict:
    """Increment the counter for each handle. Returns what was counted.

    Unknown targets are counted too rather than validated against the store:
    checking would mean a probe could learn whether a given id exists, and the
    ranker simply never joins a row that has no chunk."""
    if signal not in SIGNAL_WEIGHTS:
        raise ValueError(f"unknown signal {signal!r}; expected one of {', '.join(SIGNALS)}")
    targets = parse_targets(handles)
    if not targets:
        return {"recorded": 0, "signal": signal}

    conn.executemany(
        """
        INSERT INTO feedback (kind, target_id, signal, count, last_seen)
        VALUES (?, ?, ?, 1, date('now'))
        ON CONFLICT (kind, target_id, signal)
        DO UPDATE SET count = count + 1, last_seen = date('now')
        """,
        [(kind, rowid, signal) for kind, rowid in targets],
    )
    conn.commit()
    return {"recorded": len(targets), "signal": signal}


def _net(rows) -> dict[int, float]:
    """Weighted net signal per target id."""
    out: dict[int, float] = {}
    for row in rows:
        out[row["target_id"]] = out.get(row["target_id"], 0.0) + (
            SIGNAL_WEIGHTS.get(row["signal"], 0.0) * row["count"]
        )
    return out


def usefulness(
    conn: sqlite3.Connection, chunk_ids: list[int], doc_of: dict[int, int] | None = None
) -> dict[int, float]:
    """Net usefulness per chunk: its own signal plus its document's.

    Returns ``{}`` when capture is off or nothing has been recorded, which is
    what makes this a true no-op on a store that never opted in."""
    if not enabled() or not chunk_ids:
        return {}

    chunk_marks = ",".join("?" * len(chunk_ids))
    chunk_net = _net(
        conn.execute(
            f"SELECT target_id, signal, count FROM feedback "
            f"WHERE kind = 'chk' AND target_id IN ({chunk_marks})",
            chunk_ids,
        )
    )

    doc_net: dict[int, float] = {}
    doc_ids = sorted({d for d in (doc_of or {}).values() if d is not None})
    if doc_ids:
        doc_marks = ",".join("?" * len(doc_ids))
        doc_net = _net(
            conn.execute(
                f"SELECT target_id, signal, count FROM feedback "
                f"WHERE kind = 'doc' AND target_id IN ({doc_marks})",
                doc_ids,
            )
        )

    out: dict[int, float] = {}
    for cid in chunk_ids:
        total = chunk_net.get(cid, 0.0) + doc_net.get((doc_of or {}).get(cid), 0.0)
        if total:
            out[cid] = total
    return out


def usefulness_factor(net: float | None) -> float:
    """Turn net signal into a bounded ranking multiplier.

    Saturating, so a page cited a thousand times cannot bury everything else,
    and gentle, so one stray click barely moves anything: the factor stays
    inside 1 ± W_USEFULNESS and needs USEFULNESS_SMOOTH net signal to reach
    half of that."""
    if not net:
        return 1.0
    magnitude = math.log2(1 + abs(net))
    saturated = magnitude / (magnitude + math.log2(1 + USEFULNESS_SMOOTH))
    return 1.0 + W_USEFULNESS * saturated * (1 if net > 0 else -1)


def stats(conn: sqlite3.Connection, *, top: int = 20) -> dict:
    """What the store has learned, for the CLI and for anyone auditing what is
    held. Reads even when capture is off, so turning it off does not hide what
    was already collected."""
    totals = {
        row["signal"]: row["n"]
        for row in conn.execute("SELECT signal, SUM(count) AS n FROM feedback GROUP BY signal")
    }
    rows = conn.execute(
        """
        SELECT f.kind, f.target_id, SUM(f.count) AS events, MAX(f.last_seen) AS last_seen,
               COALESCE(d.url, dc.url) AS url
        FROM feedback f
        LEFT JOIN document d  ON f.kind = 'doc' AND d.id = f.target_id
        LEFT JOIN chunk ch    ON f.kind = 'chk' AND ch.id = f.target_id
        LEFT JOIN document dc ON dc.id = ch.document_id
        GROUP BY f.kind, f.target_id
        ORDER BY events DESC
        LIMIT ?
        """,
        (top,),
    ).fetchall()
    return {
        "enabled": enabled(),
        "totals": totals,
        "targets": [
            {"handle": ids.encode(r["kind"], r["target_id"]), "events": r["events"],
             "last_seen": r["last_seen"], "url": r["url"]}
            for r in rows
        ],
    }


def forget(conn: sqlite3.Connection) -> int:
    """Delete every recorded signal. Returns how many rows went."""
    deleted = conn.execute("DELETE FROM feedback").rowcount
    conn.commit()
    return deleted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="app.feedback", description="Inspect or clear the local usefulness signal"
    )
    parser.add_argument("--top", type=int, default=20, help="how many sources to list")
    parser.add_argument("--forget", action="store_true", help="delete everything recorded")
    args = parser.parse_args(argv)

    from .index.vector import connect

    conn = connect()
    try:
        if args.forget:
            print(f"deleted {forget(conn)} row(s)")
            return 0
        data = stats(conn, top=args.top)
        state = "on" if data["enabled"] else "off (MOO_FEEDBACK=1 to collect)"
        print(f"feedback capture: {state}")
        print(f"totals: {data['totals'] or 'nothing recorded'}")
        for row in data["targets"]:
            print(f"  {row['events']:>5}  {row['handle']:<12} {row['url'] or '(unknown)'}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

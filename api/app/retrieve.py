"""Hybrid retrieval: vector + BM25 fused with reciprocal rank fusion (SUP-80).

``retrieve()`` runs both indexes, merges the ranked lists with RRF
(score = Σ 1/(RRF_K + rank)), then collapses *near*-duplicate candidates with
simhash so mirrored or reposted content can't fake consensus — each cluster
keeps its best-ranked chunk as canonical and links the rest as ``alternates``
(evidence counting later must see one voice, not three copies).

Exact duplicates were already collapsed at chunk time (canonical_chunk_id);
simhash here catches the near-misses (quote wrappers, small edits). It runs on
the fused candidate set only (~60 rows), so cost is negligible per query.

Multi-query fan-out (SUP-82) reuses the same RRF merge: pass several query
variants and every (index, variant) ranking becomes one more voter.

CLI:  ``uv run python -m app.retrieve "why is my query slow" [--compare]``
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field

from .index import keyword, vector

RRF_K = 60          # standard RRF constant
K_EACH = 30         # candidates pulled from each index per query variant
SIMHASH_BITS = 64
# ≤ this hamming distance -> near-duplicate. Measured on the corpus: small edits
# (one-word swap + trailing sentence) land at ~10-12, while 3000 random unrelated
# chunk pairs bottom out at 19 (median 32) — 14 splits the two populations.
SIMHASH_HAMMING = 14


# ---------------------------------------------------------------------------
# simhash
# ---------------------------------------------------------------------------

def _shingles(text: str, n: int = 3):
    words = re.findall(r"\w+", text.lower())
    if len(words) < n:
        return [" ".join(words)] if words else []
    return [" ".join(words[i : i + n]) for i in range(len(words) - n + 1)]


def simhash(text: str) -> int:
    """64-bit simhash over word 3-shingles."""
    weights = [0] * SIMHASH_BITS
    for sh in _shingles(text):
        h = int.from_bytes(hashlib.blake2b(sh.encode(), digest_size=8).digest(), "big")
        for bit in range(SIMHASH_BITS):
            weights[bit] += 1 if (h >> bit) & 1 else -1
    out = 0
    for bit, w in enumerate(weights):
        if w > 0:
            out |= 1 << bit
    return out


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


# ---------------------------------------------------------------------------
# retrieval
# ---------------------------------------------------------------------------

@dataclass
class RetrievedChunk:
    chunk_id: int
    score: float                      # fused RRF score
    text: str
    heading: str | None
    url_anchor: str
    source_type: str
    document_url: str
    title: str | None
    published_at: str | None
    author_role: str | None
    popularity: int | None
    trust_score: float | None = None                     # source trust (SUP-85)
    alternates: list[int] = field(default_factory=list)  # near-dup chunk ids


def _rrf_merge(
    rankings: list[list[int]], boost: dict[int, float] | None = None
) -> dict[int, float]:
    """Fuse ranked id lists: score(id) = Σ 1/(RRF_K + rank). Optional per-id boost."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
    if boost:
        for cid, mult in boost.items():
            if cid in scores:
                scores[cid] *= mult
    return scores


def _hydrate(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, sqlite3.Row]:
    if not chunk_ids:
        return {}
    qmarks = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"""
        SELECT ch.id, ch.text, ch.heading, ch.url_anchor, ch.canonical_chunk_id,
               d.source_type, d.url AS document_url, d.title, d.published_at,
               d.author_role, d.popularity, d.trust_score
        FROM chunk ch JOIN document d ON d.id = ch.document_id
        WHERE ch.id IN ({qmarks})
        """,
        chunk_ids,
    ).fetchall()
    return {r["id"]: r for r in rows}


def _collapse_near_dups(
    ordered: list[int], rows: dict[int, sqlite3.Row]
) -> list[tuple[int, list[int]]]:
    """Greedy simhash clustering, best-ranked chunk wins. Returns (canonical, alternates)."""
    kept: list[tuple[int, int, list[int]]] = []  # (chunk_id, simhash, alternates)
    for cid in ordered:
        row = rows.get(cid)
        if row is None:
            continue
        # exact-dup safety net: fold anything pointing at an already-kept canonical
        canon = row["canonical_chunk_id"]
        folded = False
        for kcid, khash, alts in kept:
            if canon == kcid or hamming(simhash(row["text"]), khash) <= SIMHASH_HAMMING:
                alts.append(cid)
                folded = True
                break
        if not folded:
            kept.append((cid, simhash(row["text"]), []))
    return [(cid, alts) for cid, _, alts in kept]


def retrieve(
    conn: sqlite3.Connection,
    query: str,
    *,
    k: int = 10,
    queries: list[str] | None = None,
    source_types: list[str] | None = None,
    since: str | None = None,
    source_boost: dict[str, float] | None = None,
) -> list[RetrievedChunk]:
    """Hybrid retrieval over both indexes. ``queries`` adds fan-out variants
    (SUP-82); ``source_boost`` lets query understanding weight source types
    (SUP-81), e.g. {"github_issue": 1.3} for troubleshooting queries."""
    variants = [query] + [q for q in (queries or []) if q and q != query]
    filters = {"source_types": source_types, "since": since}

    rankings: list[list[int]] = []
    for q in variants:
        rankings.append([cid for cid, _ in vector.search_text(conn, q, k=K_EACH, **filters)])
        rankings.append([cid for cid, _ in keyword.search(conn, q, k=K_EACH, **filters)])

    rows = _hydrate(conn, list({cid for r in rankings for cid in r}))
    boost: dict[int, float] | None = None
    if source_boost:
        boost = {
            cid: source_boost[row["source_type"]]
            for cid, row in rows.items()
            if row["source_type"] in source_boost
        }
    scores = _rrf_merge(rankings, boost)
    ordered = sorted(scores, key=scores.get, reverse=True)

    out: list[RetrievedChunk] = []
    for cid, alternates in _collapse_near_dups(ordered, rows):
        row = rows[cid]
        out.append(
            RetrievedChunk(
                chunk_id=cid,
                score=scores[cid],
                text=row["text"],
                heading=row["heading"],
                url_anchor=row["url_anchor"],
                source_type=row["source_type"],
                document_url=row["document_url"],
                title=row["title"],
                published_at=row["published_at"],
                author_role=row["author_role"],
                popularity=row["popularity"],
                trust_score=row["trust_score"],
                alternates=alternates,
            )
        )
        if len(out) >= k:
            break
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _print_hits(label: str, items: list[tuple[int, str, str]]) -> None:
    print(f"--- {label} ---")
    for cid, source, snippet in items:
        print(f"  #{cid:<5} {source:14} {snippet!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.retrieve", description="Hybrid retrieval")
    parser.add_argument("query")
    parser.add_argument("-k", type=int, default=8)
    parser.add_argument("--compare", action="store_true", help="also show single-index results")
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--no-expand", action="store_true",
        help="kill-switch: skip query expansion fan-out (for latency comparisons)",
    )
    args = parser.parse_args(argv)

    conn = vector.connect()
    queries: list[str] | None = None
    if not args.no_expand:
        from .expand import expand

        queries = expand(conn, args.query)
        if queries:
            print(f"[fan-out: {queries}]")
    hits = retrieve(conn, args.query, k=args.k, queries=queries)
    if args.json:
        print(json.dumps([h.__dict__ for h in hits], indent=2, default=str))
    else:
        _print_hits(
            "hybrid (RRF)",
            [(h.chunk_id, h.source_type, h.text[:70].strip()) for h in hits],
        )
        for h in hits:
            if h.alternates:
                print(f"      #{h.chunk_id} near-dups collapsed: {h.alternates}")
    if args.compare:
        rows_by_id = {}
        vec = [cid for cid, _ in vector.search_text(conn, args.query, k=args.k)]
        kw = [cid for cid, _ in keyword.search(conn, args.query, k=args.k)]
        rows_by_id = _hydrate(conn, list(set(vec + kw)))
        for label, ids in (("vector only", vec), ("bm25 only", kw)):
            _print_hits(
                label,
                [
                    (cid, rows_by_id[cid]["source_type"], rows_by_id[cid]["text"][:70].strip())
                    for cid in ids
                    if cid in rows_by_id
                ],
            )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

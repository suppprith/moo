"""Retrieval eval: precision, recall, nDCG, and what each ranking stage is worth.

Fusion, the reranker and the adaptive router all shipped on the argument that
they should help. This is where that gets checked. Every configuration runs the
same judged queries, so the table reads as an ablation: BM25 alone, vectors
alone, the hybrid RRF merge, the merge plus the trust/corroboration/recency
blend, plus query fan-out, plus reranking. A stage that does not move the
numbers is complexity to delete, and finding that out is the point.

Judgments (``relevance.json``) name documents by URL substring rather than chunk
id, since chunk ids change on every rechunk. Recall counts against the judged
chunks that actually exist in the store, so it is honest about this corpus
rather than flattering.

    uv run python -m app.eval.retrieval                      # the ablation table
    uv run python -m app.eval.retrieval --claims             # + claim accuracy
    uv run python -m app.eval.retrieval --save baseline.json
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sqlite3
import time
from pathlib import Path

log = logging.getLogger("moo.eval.retrieval")

JUDGMENTS_PATH = Path(__file__).resolve().parent / "relevance.json"
DEFAULT_K = 10
WORD = re.compile(r"[a-z0-9_]{3,}")


def load_judgments(path: Path | str = JUDGMENTS_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def grade_for(judgments: list[dict], url: str, heading: str | None) -> int:
    """The best grade any judgment gives this chunk. A judgment with a heading
    only applies to chunks under that heading, which is how one long document
    can be relevant to one query and noise for another."""
    url = (url or "").lower()
    heading = (heading or "").lower()
    best = 0
    for judgment in judgments:
        if judgment["url_contains"].lower() not in url:
            continue
        needle = judgment.get("heading_contains")
        if needle and needle.lower() not in heading:
            continue
        best = max(best, int(judgment["grade"]))
    return best


def precision_at_k(grades: list[int], k: int) -> float:
    top = grades[:k]
    if not top:
        return 0.0
    return round(sum(1 for g in top if g > 0) / k, 4)


def recall_at_k(grades: list[int], k: int, total_relevant: int) -> float | None:
    if not total_relevant:
        return None
    return round(min(1.0, sum(1 for g in grades[:k] if g > 0) / total_relevant), 4)


def dcg(grades: list[int]) -> float:
    return sum((2**g - 1) / math.log2(rank + 2) for rank, g in enumerate(grades))


def ndcg_at_k(grades: list[int], ideal: list[int], k: int) -> float | None:
    best = dcg(sorted(ideal, reverse=True)[:k])
    if best <= 0:
        return None
    return round(dcg(grades[:k]) / best, 4)


def mrr(grades: list[int]) -> float:
    for rank, grade in enumerate(grades, 1):
        if grade > 0:
            return round(1 / rank, 4)
    return 0.0


def judged_pool(conn: sqlite3.Connection, judgments: list[dict]) -> list[int]:
    """Grades for every chunk in the store that any judgment covers. This is the
    recall denominator and the ideal nDCG ranking: what perfect retrieval over
    *this* corpus could have returned.

    Grades are taken per chunk, not per judgment. Overlapping judgments (a
    document graded 1 with one heading inside it graded 3) otherwise count the
    same chunk twice and quietly inflate the denominator."""
    best: dict[int, int] = {}
    for judgment in judgments:
        rows = conn.execute(
            "SELECT ch.id, ch.heading FROM chunk ch JOIN document d ON d.id = ch.document_id "
            "WHERE lower(d.url) LIKE ?",
            (f"%{judgment['url_contains'].lower()}%",),
        ).fetchall()
        needle = (judgment.get("heading_contains") or "").lower()
        for row in rows:
            if needle and needle not in (row["heading"] or "").lower():
                continue
            grade = int(judgment["grade"])
            if grade > best.get(row["id"], 0):
                best[row["id"]] = grade
    return list(best.values())


def chunk_meta(conn: sqlite3.Connection, chunk_ids: list[int]) -> dict[int, tuple[str, str]]:
    if not chunk_ids:
        return {}
    marks = ",".join("?" * len(chunk_ids))
    rows = conn.execute(
        f"SELECT ch.id, d.url, ch.heading FROM chunk ch JOIN document d ON d.id = ch.document_id "
        f"WHERE ch.id IN ({marks})",
        chunk_ids,
    ).fetchall()
    return {row["id"]: (row["url"], row["heading"]) for row in rows}


def score_ranking(conn: sqlite3.Connection, chunk_ids: list[int], query: dict, *,
                  k: int) -> dict:
    """Turn one ranked list into the metrics for one query."""
    meta = chunk_meta(conn, chunk_ids)
    grades = [grade_for(query["judgments"], *meta.get(cid, ("", ""))) for cid in chunk_ids]
    pool = judged_pool(conn, query["judgments"])
    relevant = sum(1 for g in pool if g > 0)
    return {
        "id": query["id"],
        "p@5": precision_at_k(grades, 5),
        "p@10": precision_at_k(grades, 10),
        f"r@{k}": recall_at_k(grades, k, relevant),
        f"ndcg@{k}": ndcg_at_k(grades, pool, k),
        "mrr": mrr(grades),
        "hits": sum(1 for g in grades[:k] if g > 0),
        "judged_in_corpus": relevant,
    }


def configurations(conn: sqlite3.Connection, *, k: int) -> dict:
    """Each stage of the ranking pipeline, switched on one at a time."""
    from ..expand import heuristic_expand
    from ..index import keyword, vector
    from ..rerank import rerank
    from ..retrieve import retrieve

    def bm25(query: str) -> list[int]:
        return [cid for cid, _ in keyword.search(conn, query, k=k)]

    def vectors(query: str) -> list[int]:
        return [cid for cid, _ in vector.search_text(conn, query, k=k)]

    def hybrid(query: str) -> list[int]:
        return [h.chunk_id for h in retrieve(conn, query, k=k, fuse_signals=False)]

    def hybrid_fused(query: str) -> list[int]:
        return [h.chunk_id for h in retrieve(conn, query, k=k)]

    def hybrid_fanout(query: str) -> list[int]:
        variants = heuristic_expand(query)
        return [h.chunk_id for h in retrieve(conn, query, k=k, queries=variants)]

    def hybrid_reranked(query: str) -> list[int]:
        hits = retrieve(conn, query, k=k)
        return [h.chunk_id for h in rerank(conn, query, hits)]

    return {
        "bm25": bm25,
        "vector": vectors,
        "hybrid": hybrid,
        "hybrid+fusion": hybrid_fused,
        "hybrid+fusion+fanout": hybrid_fanout,
        "hybrid+fusion+rerank": hybrid_reranked,
    }


def _mean(values: list) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 4) if nums else None


def run_eval(conn: sqlite3.Connection, queries: list[dict], configs: dict, *,
             k: int = DEFAULT_K) -> dict:
    metrics = ("p@5", "p@10", f"r@{k}", f"ndcg@{k}", "mrr")
    out: dict[str, dict] = {}
    for name, run in configs.items():
        rows, latencies = [], []
        for query in queries:
            started = time.perf_counter()
            try:
                ranked = run(query["query"])
            except Exception as exc:  # noqa: BLE001
                log.warning("%s failed on %s: %s", name, query["id"], exc)
                ranked = []
            latencies.append((time.perf_counter() - started) * 1000)
            rows.append(score_ranking(conn, ranked, query, k=k))
        out[name] = {
            "summary": {m: _mean([r[m] for r in rows]) for m in metrics},
            "latency_ms": round(_mean(latencies) or 0, 1),
            "rows": rows,
        }
        out[name]["summary"]["queries"] = len(rows)
    return {"k": k, "n_queries": len(queries), "configs": out}


def claim_accuracy(conn: sqlite3.Connection, queries: list[dict], *, k: int = 6,
                   use_llm: bool = True) -> dict:
    """Are extracted claims true to the chunk they came from, and do they come
    from sources a human judged relevant? Token overlap is a blunt proxy for
    faithfulness, but it catches the failure that matters: a claim whose words
    are nowhere in the text it cites.

    Read ``mean_overlap`` with the extractor in mind. Without an LLM key claims
    are lifted verbatim from the chunk, so overlap is ~1.0 by construction and
    says nothing. It only becomes a real measurement once claims are written
    rather than copied. ``pct_from_judged_relevant`` and ``orphans`` are
    informative either way."""
    from ..search import search

    rows = []
    for query in queries:
        response = search(conn, query["query"], mode="claims", k=k, format="full",
                          use_llm=use_llm, live=False)
        for claim in response.get("claims", []):
            provenance = conn.execute(
                "SELECT ch.id, ch.text, ch.heading, d.url FROM claim_chunk cc "
                "JOIN chunk ch ON ch.id = cc.chunk_id "
                "JOIN document d ON d.id = ch.document_id WHERE cc.claim_id = ?",
                (claim["id"],),
            ).fetchall()
            if not provenance:
                rows.append({"query": query["id"], "overlap": 0.0, "from_relevant": False,
                             "disputed": bool(claim.get("disputed")), "orphan": True})
                continue
            claim_words = set(WORD.findall(claim["text"].lower()))
            best = 0.0
            relevant = False
            for source in provenance:
                source_words = set(WORD.findall((source["text"] or "").lower()))
                if claim_words:
                    best = max(best, len(claim_words & source_words) / len(claim_words))
                if grade_for(query["judgments"], source["url"], source["heading"]) > 0:
                    relevant = True
            rows.append({"query": query["id"], "overlap": round(best, 3),
                         "from_relevant": relevant, "disputed": bool(claim.get("disputed")),
                         "orphan": False})
    if not rows:
        return {"claims": 0}
    return {
        "claims": len(rows),
        "mean_overlap": round(sum(r["overlap"] for r in rows) / len(rows), 3),
        "pct_well_grounded": round(sum(1 for r in rows if r["overlap"] >= 0.5) / len(rows), 3),
        "pct_from_judged_relevant": round(
            sum(1 for r in rows if r["from_relevant"]) / len(rows), 3),
        "pct_disputed": round(sum(1 for r in rows if r["disputed"]) / len(rows), 3),
        "orphans": sum(1 for r in rows if r["orphan"]),
    }


def format_table(result: dict) -> str:
    k = result["k"]
    metrics = ("p@5", "p@10", f"r@{k}", f"ndcg@{k}", "mrr")
    header = f"{'configuration':<24}" + "".join(f"{m:>10}" for m in metrics) + f"{'ms':>9}"
    lines = [f"retrieval eval  (n={result['n_queries']} judged queries, k={k})", "",
             header, "-" * len(header)]
    for name, data in result["configs"].items():
        summary = data["summary"]
        cells = "".join(
            f"{'-' if summary[m] is None else format(summary[m], '.3f'):>10}" for m in metrics
        )
        lines.append(f"{name:<24}{cells}{data['latency_ms']:>9.0f}")
    return "\n".join(lines)


def format_deltas(result: dict) -> str:
    """What each stage bought, against the one before it. This is the number
    that decides whether a stage earns its complexity."""
    k = result["k"]
    order = list(result["configs"])
    lines = ["", f"stage deltas (nDCG@{k})"]
    for previous, current in zip(order, order[1:], strict=False):
        before = result["configs"][previous]["summary"][f"ndcg@{k}"]
        after = result["configs"][current]["summary"][f"ndcg@{k}"]
        if before is None or after is None:
            lines.append(f"  {current:<24} not comparable")
            continue
        delta = after - before
        verdict = "no change" if abs(delta) < 0.005 else ("better" if delta > 0 else "WORSE")
        lines.append(f"  {current:<24} {delta:+.3f} vs {previous:<22} {verdict}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.eval.retrieval",
                                     description="Retrieval metrics + ranking ablation")
    parser.add_argument("-k", type=int, default=DEFAULT_K)
    parser.add_argument("--configs", nargs="*", default=None, help="subset of configurations")
    parser.add_argument("--claims", action="store_true", help="also score claim accuracy")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--judgments", default=str(JUDGMENTS_PATH))
    parser.add_argument("--save", default=None, help="write the full result JSON here")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from ..index.vector import connect

    data = load_judgments(args.judgments)
    conn = connect()
    try:
        configs = configurations(conn, k=args.k)
        if args.configs:
            configs = {name: fn for name, fn in configs.items() if name in args.configs}
        result = run_eval(conn, data["queries"], configs, k=args.k)
        result["judgments_version"] = data.get("version")
        print(format_table(result))
        print(format_deltas(result))
        if args.claims:
            result["claim_accuracy"] = claim_accuracy(conn, data["queries"],
                                                      use_llm=not args.no_llm)
            print("\nclaim accuracy")
            for key, value in result["claim_accuracy"].items():
                print(f"  {key:<26}{value}")
    finally:
        conn.close()

    if args.save:
        Path(args.save).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\nsaved -> {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

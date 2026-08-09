"""Latency and model-cost measurement for the search pipeline.

Runs the judged query set through each mode twice. The first pass is the cold
number an operator actually pays: nothing cached, models loading on the very
first query. The second pass over the same queries is the warm number, and it
is also the cache proof, because a repeated query must reach zero LLM attempts
once every stage has cached its result.

Cost is reported in the units this can honestly measure: calls attempted and
input characters carried. Prices differ per provider and change, so the money
figure is yours to supply:

    uv run python -m app.eval.latency
    uv run python -m app.eval.latency --modes raw claims --live
    uv run python -m app.eval.latency --in-price 0.10 --out-price 0.40
    uv run python -m app.eval.latency --save app/eval/latency_baseline.json

`--in-price`/`--out-price` are dollars per million tokens from your provider's
page. Output tokens are not measured, so the money column is an upper bound: it
charges every call the full `max_tokens` cap.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import statistics
from pathlib import Path

from ..budget import CHARS_PER_TOKEN
from ..search import MODES, search
from ..timing import BUDGET_MS
from .retrieval import JUDGMENTS_PATH, load_judgments

log = logging.getLogger("moo.eval.latency")

MAX_OUTPUT_TOKENS = 1024
STUB_MODEL = "stub-provider"


def install_stub_provider() -> None:
    """Exercise the cache path with no key: a provider that answers every stage
    with an empty payload. The stages fall back to their heuristics exactly as
    they do keyless, but a result now gets cached, so the repeat pass shows what
    a real provider would be billed for the second time round, which is nothing.

    Every row it writes is stamped with STUB_MODEL and deleted by
    `clear_stub_cache` afterwards, so a stub answer can never be served to a run
    that has a real provider configured.
    """
    from .. import llm

    llm._config = {"provider": "openai", "api_key": "stub",
                   "base_url": "http://stub", "model": STUB_MODEL}
    llm._config_checked = True
    llm._BACKENDS["openai"] = lambda *a, **k: {}


def clear_stub_cache(conn: sqlite3.Connection) -> int:
    cur = conn.execute("DELETE FROM llm_cache WHERE model = ?", (STUB_MODEL,))
    conn.commit()
    return cur.rowcount


def measure(conn: sqlite3.Connection, query: str, mode: str, *, live: bool | None = False,
            use_llm: bool = True, k: int = 10) -> dict:
    result = search(conn, query, mode=mode, k=k, live=live, use_llm=use_llm)
    meta = result["meta"]
    return {
        "query": query,
        "mode": mode,
        "elapsed_ms": meta["elapsed_ms"],
        "timings_ms": meta["timings_ms"],
        "cost": meta["cost"],
    }


def _percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, int(round((p / 100) * (len(ordered) - 1))))
    return round(ordered[idx], 1)


def _aggregate(runs: list[dict]) -> dict:
    times = [r["elapsed_ms"] for r in runs]
    stages: dict[str, list[float]] = {}
    for r in runs:
        for name, ms in r["timings_ms"].items():
            stages.setdefault(name, []).append(ms)
    return {
        "queries": len(runs),
        "p50_ms": _percentile(times, 50),
        "p95_ms": _percentile(times, 95),
        "max_ms": round(max(times), 1) if times else 0.0,
        "stage_median_ms": {n: round(statistics.median(v), 1) for n, v in sorted(stages.items())},
        "llm_calls": sum(r["cost"]["llm_calls"] for r in runs),
        "llm_attempts": sum(r["cost"]["llm_attempts"] for r in runs),
        "cache_hits": sum(r["cost"]["cache_hits"] for r in runs),
        "prompt_chars": sum(r["cost"]["prompt_chars"] for r in runs),
    }


def cost_estimate(agg: dict, in_price: float, out_price: float) -> dict:
    """Per-query model cost. Input tokens are measured; output is charged at the
    full cap, so the dollar figure is a ceiling, not an average."""
    queries = max(agg["queries"], 1)
    in_tokens = agg["prompt_chars"] / CHARS_PER_TOKEN / queries
    out_tokens = agg["llm_attempts"] * MAX_OUTPUT_TOKENS / queries
    return {
        "llm_attempts_per_query": round(agg["llm_attempts"] / queries, 2),
        "input_tokens_per_query": round(in_tokens, 1),
        "max_output_tokens_per_query": round(out_tokens, 1),
        "usd_per_query_upper_bound": round(
            in_tokens / 1_000_000 * in_price + out_tokens / 1_000_000 * out_price, 6
        ),
    }


def run_latency(conn: sqlite3.Connection, queries: list[str], modes=MODES, *,
                live: bool | None = False, use_llm: bool = True, k: int = 10,
                in_price: float = 0.0, out_price: float = 0.0) -> dict:
    """Cold pass then warm pass per mode, aggregated with the budget verdict."""
    out: dict = {"queries": len(queries), "modes": {}, "live": live, "use_llm": use_llm}
    for mode in modes:
        cold = [measure(conn, q, mode, live=live, use_llm=use_llm, k=k) for q in queries]
        warm = [measure(conn, q, mode, live=live, use_llm=use_llm, k=k) for q in queries]
        cold_agg, warm_agg = _aggregate(cold), _aggregate(warm)
        budget = BUDGET_MS.get(mode)
        out["modes"][mode] = {
            "cold": cold_agg,
            "warm": warm_agg,
            "budget_ms": budget,
            "within_budget": budget is None or warm_agg["p95_ms"] <= budget,
            "repeat_is_free": warm_agg["llm_attempts"] == 0,
            "cost": cost_estimate(cold_agg, in_price, out_price),
        }
    return out


def format_table(result: dict) -> str:
    lines = [
        f"latency over {result['queries']} queries "
        f"(live={result['live']}, use_llm={result['use_llm']})",
        f"{'mode':<8}{'cold p50':>10}{'cold p95':>10}{'warm p50':>10}{'warm p95':>10}"
        f"{'budget':>9}{'verdict':>10}{'repeat':>10}",
    ]
    for mode, row in result["modes"].items():
        budget = row["budget_ms"]
        lines.append(
            f"{mode:<8}{row['cold']['p50_ms']:>10.0f}{row['cold']['p95_ms']:>10.0f}"
            f"{row['warm']['p50_ms']:>10.0f}{row['warm']['p95_ms']:>10.0f}"
            f"{(budget or 0):>9.0f}"
            f"{('within' if row['within_budget'] else 'OVER'):>10}"
            f"{('free' if row['repeat_is_free'] else 'billed'):>10}"
        )
    for mode, row in result["modes"].items():
        cost = row["cost"]
        lines.append(
            f"\n{mode} cold cost per query: {cost['llm_attempts_per_query']} llm calls, "
            f"{cost['input_tokens_per_query']:.0f} input tokens, "
            f"<= {cost['max_output_tokens_per_query']:.0f} output tokens, "
            f"<= ${cost['usd_per_query_upper_bound']:.6f}"
        )
        stages = row["warm"]["stage_median_ms"]
        lines.append("  warm stages: " + " ".join(f"{n}={ms:.0f}ms" for n, ms in stages.items()))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.eval.latency",
                                     description="Pipeline latency + model cost")
    parser.add_argument("--modes", nargs="*", default=list(MODES), choices=list(MODES))
    parser.add_argument("-k", type=int, default=10)
    parser.add_argument("--live", action="store_true", help="allow live web fetch (slower, real)")
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--limit", type=int, default=None, help="first N judged queries")
    parser.add_argument("--in-price", type=float, default=0.0,
                        help="USD per million input tokens, from your provider")
    parser.add_argument("--out-price", type=float, default=0.0,
                        help="USD per million output tokens, from your provider")
    parser.add_argument("--stub-provider", action="store_true",
                        help="fake a provider so the LLM cache path is measurable without a key")
    parser.add_argument("--save", default=None)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from ..index.vector import connect

    if args.stub_provider:
        install_stub_provider()

    queries = [q["query"] for q in load_judgments(JUDGMENTS_PATH)["queries"]]
    if args.limit:
        queries = queries[: args.limit]

    conn = connect()
    dropped = 0
    try:
        result = run_latency(
            conn, queries, args.modes, live=None if args.live else False,
            use_llm=not args.no_llm, k=args.k,
            in_price=args.in_price, out_price=args.out_price,
        )
    finally:
        if args.stub_provider:
            dropped = clear_stub_cache(conn)
        conn.close()
    if dropped:
        print(f"cleared {dropped} stub cache rows")

    from .. import llm

    result["provider"] = {
        "llm": bool(llm.get_client()),
        "model": llm.model_name(),
        "stubbed": args.stub_provider,
    }
    print(format_table(result))
    if args.save:
        Path(args.save).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\nsaved -> {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

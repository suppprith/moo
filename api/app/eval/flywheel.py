"""Continuous competitive-eval flywheel (SUP-141).

Turns the one-shot benchmark into a standing loop: every run scores each
available engine (moo always; Exa/Tavily when their keys are set) on the
versioned golden task set, appends a timestamped record to a local history
file, and a **regression gate** fails when moo drops below its own last run —
so quality changes are caught at commit time, not in production.

    uv run python -m app.eval.flywheel              # run + append history
    uv run python -m app.eval.flywheel --gate       # exit 1 on moo regression

Metrics per engine (higher is better unless noted): ``coverage`` (rubric
points present in returned text), ``source_recall`` (expected source hosts
present in returned urls), ``latency_ms`` (lower is better; informational),
``errors``. History lives in ``api/data/eval_history.jsonl`` (gitignored —
machine-local trend data, not source).

Keyless honesty: without competitor keys this is moo-vs-its-own-history,
which is exactly what the regression gate needs; the head-to-head columns
appear as soon as keys exist. No query content beyond the committed golden
tasks is ever recorded.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ..db import API_DIR
from .benchmark import _avg, _fraction_present, load_tasks
from .competitors import engines

HISTORY_PATH = API_DIR / "data" / "eval_history.jsonl"

# gate: fail when the new value is below previous * threshold
GATE_METRICS = ("coverage", "source_recall")
GATE_THRESHOLD = 0.9


# -- scoring (one engine, one task) --------------------------------------------

def score_engine_output(out: dict, task: dict) -> dict:
    """Score a normalized engine response against a golden task."""
    text = " ".join(
        f"{r.get('title', '')} {r.get('snippet', '')}" for r in out.get("results", [])
    )
    if out.get("answer"):
        text += " " + out["answer"]
    urls = " ".join(r.get("url", "") for r in out.get("results", []))
    return {
        "coverage": _fraction_present(task.get("rubric_points", []), text.lower()),
        "source_recall": _fraction_present(task.get("key_source_hints", []), urls.lower()),
    }


def run_flywheel(conn, *, tasks: list[dict] | None = None, engine_map: dict | None = None) -> dict:
    """Run every engine over every task. Returns the timestamped record."""
    task_set = load_tasks()
    tasks = tasks if tasks is not None else task_set["tasks"]
    engine_map = engine_map if engine_map is not None else engines(conn)

    per_engine: dict[str, dict] = {}
    for name, run in engine_map.items():
        rows, errors, latencies = [], 0, []
        for task in tasks:
            t0 = time.perf_counter()
            out = run(task["question"])
            latencies.append((time.perf_counter() - t0) * 1000)
            if out is None:
                errors += 1
                continue
            rows.append(score_engine_output(out, task))
        per_engine[name] = {
            "coverage": _avg([r["coverage"] for r in rows]),
            "source_recall": _avg([r["source_recall"] for r in rows]),
            "latency_ms": round(_avg(latencies) or 0, 1),
            "tasks": len(rows),
            "errors": errors,
        }

    return {
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "task_set_version": task_set.get("version"),
        "engines": per_engine,
    }


# -- history + regression gate ----------------------------------------------------

def append_history(record: dict, path: Path | str = HISTORY_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def load_history(path: Path | str = HISTORY_PATH) -> list[dict]:
    path = Path(path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # a corrupt line never breaks the gate
    return out


def check_regression(record: dict, history: list[dict], *,
                     engine: str = "moo", threshold: float = GATE_THRESHOLD) -> list[str]:
    """Compare ``record`` against the most recent prior entry that has the
    engine. Returns failure messages (empty = pass). First run always passes."""
    current = record.get("engines", {}).get(engine)
    if current is None:
        return [f"engine {engine!r} missing from current run"]
    previous = None
    for entry in reversed(history):
        prev = entry.get("engines", {}).get(engine)
        if prev is not None:
            previous = prev
            break
    if previous is None:
        return []  # nothing to regress against yet
    failures = []
    for metric in GATE_METRICS:
        cur, prev_v = current.get(metric), previous.get(metric)
        if cur is None or prev_v is None or prev_v == 0:
            continue
        if cur < prev_v * threshold:
            failures.append(
                f"{engine}.{metric} regressed: {cur:.3f} < {threshold:.0%} of previous {prev_v:.3f}"
            )
    return failures


def format_table(record: dict) -> str:
    lines = [f"eval flywheel @ {record['at']} (task set v{record['task_set_version']})"]
    header = f"{'engine':<10} {'coverage':>9} {'src_recall':>11} {'latency_ms':>11} {'errors':>7}"
    lines += [header, "-" * len(header)]
    for name, m in record["engines"].items():
        cov = f"{m['coverage']:.3f}" if m["coverage"] is not None else "-"
        rec = f"{m['source_recall']:.3f}" if m["source_recall"] is not None else "-"
        lines.append(f"{name:<10} {cov:>9} {rec:>11} {m['latency_ms']:>11} {m['errors']:>7}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.eval.flywheel",
                                     description="Competitive eval + regression gate")
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 if moo regressed vs its previous run")
    parser.add_argument("--no-history", action="store_true", help="don't append this run")
    args = parser.parse_args(argv)

    from ..index.vector import connect

    conn = connect()
    try:
        record = run_flywheel(conn)
    finally:
        conn.close()

    print(format_table(record))
    history = load_history()
    failures = check_regression(record, history) if args.gate else []
    if not args.no_history:
        append_history(record)
    for msg in failures:
        print(f"REGRESSION: {msg}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

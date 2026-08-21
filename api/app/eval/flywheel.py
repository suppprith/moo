"""Continuous competitive-eval flywheel.

Turns the one-shot benchmark into a standing loop: every run scores each
available engine (moo always; Exa/Tavily when their keys are set) on the
versioned golden task set, appends a timestamped record to a local history
file, and a **regression gate** fails when moo drops below its own last run —
so quality changes are caught at commit time, not in production.

    uv run python -m app.eval.flywheel                     # run + append history
    uv run python -m app.eval.flywheel --gate              # exit 1 on moo regression
    uv run python -m app.eval.flywheel --report            # + rewrite the trend report

Metrics per engine (higher is better unless noted): ``coverage`` (rubric
points present in returned text), ``source_recall`` (expected source hosts
present in returned urls), ``latency_ms`` (lower is better; informational),
``errors``. History lives in ``api/data/eval_history.jsonl`` (gitignored —
machine-local trend data, not source); the rendered report is committed, so
the trend is reviewable without shipping a database.

Scores only compare within a task-set version. The golden set is meant to grow
(see ``growth_process`` in ``deep_research_tasks.json``), and a bigger set
legitimately scores differently, so the gate compares a run only against the
most recent run of the *same* version — growing the set can never fire a false
regression.

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
REPORT_PATH = API_DIR.parent / "docs" / "eval-trends.md"

GATE_METRICS = ("coverage", "source_recall")
GATE_THRESHOLD = 0.9
REPORT_RUNS = 10


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
        "n_tasks": len(tasks),
        "conditions": run_conditions(),
        "engines": per_engine,
    }


def run_conditions() -> dict:
    """What moo had available for this run. Recorded because the same task set
    scores very differently store-only vs. live, and a trend line that doesn't
    say which one it is means nothing."""
    from .. import llm
    from ..live.providers import resolve_provider

    return {"live_search": resolve_provider() is not None, "llm": llm.get_client() is not None}


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
                continue
    return out


def previous_comparable(record: dict, history: list[dict], *, engine: str = "moo") -> dict | None:
    """The most recent prior run of the same task-set version that scored
    ``engine``. Different versions are different yardsticks, so a run is never
    compared across them and growing the golden set is not a regression."""
    version = record.get("task_set_version")
    for entry in reversed(history):
        if entry.get("task_set_version") != version:
            continue
        prev = entry.get("engines", {}).get(engine)
        if prev is not None:
            return prev
    return None


def check_regression(record: dict, history: list[dict], *,
                     engine: str = "moo", threshold: float = GATE_THRESHOLD) -> list[str]:
    """Compare ``record`` against the most recent prior run of the same task-set
    version. Returns failure messages (empty = pass). The first run at a given
    version always passes — there is nothing comparable to measure it against."""
    current = record.get("engines", {}).get(engine)
    if current is None:
        return [f"engine {engine!r} missing from current run"]
    previous = previous_comparable(record, history, engine=engine)
    if previous is None:
        return []
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


def _cell(value) -> str:
    if value is None:
        return "—"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _ms(value) -> str:
    return "—" if value is None else f"{round(float(value)):,}"


def _conditions_line(record: dict) -> str:
    cond = record.get("conditions") or {}
    if not cond:
        return "- Conditions: not recorded for this run"
    live = "live crawl ON" if cond.get("live_search") else "store/cache only (no search provider)"
    llm = "LLM stages ON" if cond.get("llm") else "heuristic fallbacks (no LLM key)"
    return f"- Conditions: {live}, {llm}"


def render_report(history: list[dict], *, limit: int = REPORT_RUNS) -> str:
    """Render the committed trend report: the latest head-to-head, then each
    gated metric over the last ``limit`` runs of the current task-set version."""
    if not history:
        return (
            "# Eval trends\n\n"
            "No runs recorded yet. Run `uv run python -m app.eval.flywheel --report`\n"
            "to populate this page.\n"
        )

    latest = history[-1]
    version = latest.get("task_set_version")
    same_version = [h for h in history if h.get("task_set_version") == version][-limit:]
    engine_names: list[str] = []
    for entry in same_version:
        for name in entry.get("engines", {}):
            if name not in engine_names:
                engine_names.append(name)

    out = [
        "# Eval trends",
        "",
        "Generated by `app.eval.flywheel --report`; do not edit by hand. Every run",
        "scores each available engine on the golden task set in",
        "[`deep_research_tasks.json`](../api/app/eval/deep_research_tasks.json).",
        "Competitor columns appear when `EXA_API_KEY` / `TAVILY_API_KEY` are set; moo",
        "is always scored, so the trend stays continuous whether or not anyone is",
        "paying for a comparison that day.",
        "",
        f"- Latest run: `{latest.get('at')}`",
        f"- Task set: v{version} ({latest.get('n_tasks', '?')} tasks)",
        f"- Runs shown: {len(same_version)} (this task-set version only)",
        _conditions_line(latest),
        "",
        "## Latest head-to-head",
        "",
        "| engine | coverage | source recall | latency ms | tasks | errors |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for name, m in latest.get("engines", {}).items():
        out.append(
            f"| {name} | {_cell(m.get('coverage'))} | {_cell(m.get('source_recall'))} "
            f"| {_ms(m.get('latency_ms'))} | {_cell(m.get('tasks'))} "
            f"| {_cell(m.get('errors'))} |"
        )

    for metric in GATE_METRICS:
        out += [
            "",
            f"## {metric.replace('_', ' ')} over time",
            "",
            "| run | " + " | ".join(engine_names) + " |",
            "| --- |" + " ---: |" * len(engine_names),
        ]
        for entry in same_version:
            cells = [
                _cell(entry.get("engines", {}).get(name, {}).get(metric))
                for name in engine_names
            ]
            out.append(f"| {entry.get('at')} | " + " | ".join(cells) + " |")

    out += [
        "",
        "## Regression gate",
        "",
        f"`--gate` fails the run when moo drops below {GATE_THRESHOLD:.0%} of its previous",
        f"score on {' or '.join(GATE_METRICS)}, comparing only against runs of the same",
        "task-set version. Growing the golden set is therefore free: the first run at a",
        "new version has nothing to regress against, and later runs compare like with",
        "like.",
        "",
    ]
    return "\n".join(out)


def write_report(history: list[dict], path: Path | str = REPORT_PATH, *,
                 limit: int = REPORT_RUNS) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(history, limit=limit), encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.eval.flywheel",
                                     description="Competitive eval + regression gate")
    parser.add_argument("--gate", action="store_true",
                        help="exit 1 if moo regressed vs its previous run")
    parser.add_argument("--no-history", action="store_true", help="don't append this run")
    parser.add_argument("--history", default=str(HISTORY_PATH), help="history jsonl path")
    parser.add_argument("--report", nargs="?", const=str(REPORT_PATH), default=None,
                        metavar="PATH",
                        help=f"write the markdown trend report (default: {REPORT_PATH.name})")
    parser.add_argument("--report-only", action="store_true",
                        help="re-render the report from history without running any engine")
    args = parser.parse_args(argv)

    if args.report_only:
        print(f"report -> {write_report(load_history(args.history), args.report or REPORT_PATH)}")
        return 0

    from ..index.vector import connect

    conn = connect()
    try:
        record = run_flywheel(conn)
    finally:
        conn.close()

    print(format_table(record))
    history = load_history(args.history)
    failures = check_regression(record, history) if args.gate else []
    if not args.no_history:
        append_history(record, args.history)
    if args.report:
        print(f"report -> {write_report([*history, record], args.report)}")
    for msg in failures:
        print(f"REGRESSION: {msg}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

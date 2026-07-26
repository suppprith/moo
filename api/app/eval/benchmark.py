"""Deep-research benchmark.

Scores the deep-research engine on a versioned task set
(``deep_research_tasks.json``) and compares single-shot ``search`` against
``deep_research``:

- **coverage** — fraction of a task's rubric points that appear in the output
- **citation_accuracy** — fraction of findings that are grounded
- **source_recall** — fraction of the task's expected source domains cited
- **contradiction_recall** — did it surface the task's known disputes?
- **tokens** / **steps** / **elapsed_ms** — cost per task

Scoring functions are pure (keyword/substring matching over the report), so they
are unit-testable without running the pipeline. The runner produces a comparison
table; ``--save`` records a baseline so every engine change gets a before/after.

CLI:  ``uv run python -m app.eval.benchmark [--max-steps N] [--save baseline.json]``
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path

from ..budget import estimate_tokens

log = logging.getLogger("moo.eval")

_TASKS_PATH = Path(__file__).resolve().parent / "deep_research_tasks.json"


def load_tasks(path: Path | str = _TASKS_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _text_blob(report: dict) -> str:
    parts = [report.get("executive_answer") or ""]
    parts += [f.get("text", "") for f in report.get("findings", [])]
    return " ".join(parts).lower()


def _fraction_present(needles: list[str], haystack: str) -> float | None:
    if not needles:
        return None
    hits = sum(1 for n in needles if n.lower() in haystack)
    return round(hits / len(needles), 3)


def score_coverage(report: dict, rubric_points: list[str]) -> float | None:
    return _fraction_present(rubric_points, _text_blob(report))


def score_source_recall(report: dict, hints: list[str]) -> float | None:
    urls = " ".join((s.get("url") or "") for s in report.get("sources", [])).lower()
    return _fraction_present(hints, urls)


def score_contradiction_recall(report: dict, disputed_hints: list[str]) -> float | None:
    if not disputed_hints:
        return None
    if not report.get("disputed_points"):
        return 0.0
    dp = " ".join(d.get("text", "") for d in report["disputed_points"]).lower()
    return _fraction_present(disputed_hints, dp)


def citation_accuracy(report: dict) -> float | None:
    return report.get("groundedness", {}).get("pct_grounded")


def score_report(report: dict, task: dict) -> dict:
    return {
        "coverage": score_coverage(report, task.get("rubric_points", [])),
        "citation_accuracy": citation_accuracy(report),
        "source_recall": score_source_recall(report, task.get("key_source_hints", [])),
        "contradiction_recall": score_contradiction_recall(report, task.get("disputed_hints", [])),
        "tokens": estimate_tokens(report),
    }


def _search_as_report(sr: dict) -> dict:
    """Adapt a full-format search response into a report-shaped dict for scoring."""
    return {
        "executive_answer": sr.get("answer") or "",
        "findings": [{"text": c.get("text", "")} for c in sr.get("claims", [])],
        "sources": sr.get("sources", []),
        "disputed_points": [{"text": c.get("text", "")} for c in sr.get("claims", []) if c.get("disputed")],
        "groundedness": {},
    }


def _avg(values: list) -> float | None:
    nums = [v for v in values if isinstance(v, (int, float))]
    return round(sum(nums) / len(nums), 3) if nums else None


def run_benchmark(conn, tasks: list[dict], *, k: int = 5, max_steps: int = 4,
                  max_seconds: float = 120.0, use_llm: bool = True) -> dict:
    """Run every task through deep_research and single-shot search; return
    per-task rows + aggregate averages for both."""
    from ..research.report import assemble_report
    from ..research.session import delete_run, run_research
    from ..search import search

    rows = []
    for task in tasks:
        run = run_research(conn, task["question"], k=k, max_steps=max_steps,
                           max_seconds=max_seconds, use_llm=use_llm)
        report = assemble_report(conn, run, use_llm=use_llm)
        dr = score_report(report, task)
        dr.update(steps=run["budget"]["steps_used"], elapsed_ms=run["budget"]["elapsed_ms"],
                  status=run["status"])
        delete_run(conn, run["run_id"])

        sr = search(conn, task["question"], mode="claims", k=k, format="full", use_llm=use_llm)
        ss = score_report(_search_as_report(sr), task)

        rows.append({"id": task["id"], "deep_research": dr, "search": ss})

    def agg(engine, metric):
        return _avg([r[engine][metric] for r in rows])

    metrics = ("coverage", "citation_accuracy", "source_recall", "contradiction_recall", "tokens")
    summary = {
        "deep_research": {m: agg("deep_research", m) for m in metrics},
        "search": {m: agg("search", m) for m in metrics},
    }
    return {"n_tasks": len(rows), "k": k, "max_steps": max_steps, "rows": rows, "summary": summary}


def format_table(result: dict) -> str:
    s = result["summary"]
    metrics = ("coverage", "citation_accuracy", "source_recall", "contradiction_recall", "tokens")
    lines = [f"deep-research benchmark  (n={result['n_tasks']}, k={result['k']}, max_steps={result['max_steps']})", ""]
    lines.append(f"{'metric':<22}{'search':>12}{'deep_research':>16}")
    lines.append("-" * 50)
    for m in metrics:
        lines.append(f"{m:<22}{str(s['search'][m]):>12}{str(s['deep_research'][m]):>16}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.eval.benchmark", description="Deep-research benchmark")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--max-steps", type=int, default=4)
    parser.add_argument("--max-seconds", type=float, default=120.0)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--tasks", default=str(_TASKS_PATH))
    parser.add_argument("--save", default=None, help="write full results JSON to this path")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from ..index.vector import connect

    data = load_tasks(args.tasks)
    conn = connect()
    result = run_benchmark(conn, data["tasks"], k=args.k, max_steps=args.max_steps,
                           max_seconds=args.max_seconds, use_llm=not args.no_llm)
    conn.close()
    result["task_set_version"] = data.get("version")
    print(format_table(result))
    if args.save:
        Path(args.save).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\nsaved -> {args.save}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

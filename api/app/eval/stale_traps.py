"""The USP acceptance gate: does moo catch stale answers that others repeat?

moo's one falsifiable claim is that when a query's popular answer is out of date,
moo returns the current one *and* says the old one is superseded, with evidence
on both sides. This module is the test of that sentence, not a general quality
benchmark.

A trap (``stale_trap_tasks.json``) is a real query plus the answer that is still
everywhere and the answer that is actually current. An engine **passes** a trap
when it surfaces the current answer and flags the old one; repeating the old
answer with no warning is the failure being measured. Flagging counts two ways:

- **structurally** — a disputed point, a superseded claim, a version-outdated
  source. Only moo emits these, which is the point.
- **textually** — the response names the old thing next to a staleness cue
  ("deprecated", "removed in", "no longer"). Any engine can do this, and a
  competitor that does it passes the trap honestly.

Scoring is pure over normalized payloads, so the whole grid is unit-testable
without keys or network. Competitor columns appear when their keys are set.

    uv run python -m app.eval.stale_traps                  # every available engine
    uv run python -m app.eval.stale_traps --engines moo_fast --limit 5
    uv run python -m app.eval.stale_traps --gate           # exit 1 if the USP fails
    uv run python -m app.eval.stale_traps --gate --strict  # ...or if it never ran
    uv run python -m app.eval.stale_traps --transcript docs/stale-answer-demo.md

The gate has three outcomes, not two: pass, fail, and **not evaluated**. Every
trap asks what the live web says today, so without a search provider nothing is
fetched and every engine scores 0% — which says nothing about the USP. Reporting
that as a failure would cry wolf; reporting it as a pass would be a lie.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from pathlib import Path

from .benchmark import _avg

log = logging.getLogger("moo.eval.stale")

TASKS_PATH = Path(__file__).resolve().parent / "stale_trap_tasks.json"

PASS_BAR = 0.70
COMPETITOR_MARGIN = 0.30
SNIPPET_CHARS = 240


def load_traps(path: Path | str = TASKS_PATH) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def engine_text(out: dict) -> str:
    """Everything an engine said, lowercased: result rows, an answer if it wrote
    one, and a report's findings and disputed points."""
    parts: list[str] = []
    for row in out.get("results") or []:
        parts += [str(row.get("title") or ""), str(row.get("snippet") or "")]
        parts += [str(h) for h in (row.get("highlights") or [])]
    if out.get("answer"):
        parts.append(str(out["answer"]))
    if out.get("executive_answer"):
        parts.append(str(out["executive_answer"]))
    for finding in out.get("findings") or []:
        parts.append(str(finding.get("text") or ""))
    for point in out.get("disputed_points") or []:
        parts.append(str(point.get("text") or ""))
    for claim in _claims(out):
        parts.append(str(claim.get("text") or ""))
    return " ".join(parts).lower()


def _claims(out: dict) -> list[dict]:
    """Claims wherever they ride: a search response, or the evidence block a
    web-search response carries at depth=claims."""
    claims = list(out.get("claims") or [])
    evidence = out.get("evidence") or {}
    claims += list(evidence.get("claims") or [])
    return [c for c in claims if isinstance(c, dict)]


def structural_flags(out: dict) -> dict:
    """Machine-readable staleness signals. These are the ones a competitor
    returning `{title, url, snippet}` structurally cannot produce."""
    disputed = [p for p in (out.get("disputed_points") or []) if isinstance(p, dict)]
    claims = _claims(out)
    superseded = [c for c in claims if c.get("superseded_by")]
    superseded += [f for f in (out.get("findings") or [])
                   if isinstance(f, dict) and f.get("superseded_by")]
    validity = [c for c in claims + list(out.get("findings") or [])
                if isinstance(c, dict) and (c.get("valid") or {}).get("until")]
    outdated = [r for r in (out.get("results") or []) if r.get("version_outdated")]
    outdated += [s for s in (out.get("sources") or [])
                 if isinstance(s, dict) and s.get("version_outdated")]
    return {
        "disputed": len(disputed) + sum(1 for c in claims if c.get("disputed")),
        "superseded": len(superseded),
        "version_validity": len(validity),
        "version_outdated_sources": len(outdated),
        "two_sided": sum(1 for p in disputed if p.get("supports") and p.get("contradicts")),
    }


def score_trap(out: dict | None, trap: dict, cues: list[str]) -> dict:
    """Score one engine's answer to one trap. ``out=None`` means the engine
    errored, which scores as a miss rather than being dropped."""
    if out is None:
        return {"id": trap["id"], "error": True, "surfaces_current": False,
                "repeats_stale": False, "flags_stale": False, "passes": False,
                "silent_stale": False, "two_sided": False, "signals": {}, "quote": None}

    text = engine_text(out)
    current = any(m.lower() in text for m in trap["current"]["markers"])
    stale = any(m.lower() in text for m in trap["stale"]["markers"])
    cue_hits = [c for c in cues if c in text]
    signals = structural_flags(out)
    structural = any(signals[k] for k in
                     ("disputed", "superseded", "version_validity", "version_outdated_sources"))
    textual = stale and bool(cue_hits)
    flagged = bool(structural or textual)

    return {
        "id": trap["id"],
        "error": False,
        "surfaces_current": current,
        "repeats_stale": stale,
        "flags_stale": flagged,
        "flagged_structurally": bool(structural),
        "passes": bool(current and flagged),
        "silent_stale": bool(stale and not flagged),
        "two_sided": bool(signals["two_sided"]),
        "signals": signals,
        "cues": cue_hits[:3],
        "quote": _quote(text, trap),
    }


def _quote(text: str, trap: dict) -> str | None:
    """A short window around the first marker, so a transcript shows the words
    the score was based on rather than asking anyone to take it on faith."""
    for marker in trap["current"]["markers"] + trap["stale"]["markers"]:
        at = text.find(marker.lower())
        if at != -1:
            start = max(0, at - SNIPPET_CHARS // 3)
            return " ".join(text[start:start + SNIPPET_CHARS].split())
    return None


def summarize(rows: list[dict]) -> dict:
    scored = [r for r in rows if not r["error"]]
    return {
        "n": len(rows),
        "errors": sum(1 for r in rows if r["error"]),
        "pass_rate": _rate(rows, "passes"),
        "current_rate": _rate(rows, "surfaces_current"),
        "flag_rate": _rate(rows, "flags_stale"),
        "structural_flag_rate": _rate(scored, "flagged_structurally"),
        "silent_stale_rate": _rate(rows, "silent_stale"),
        "two_sided_rate": _rate(rows, "two_sided"),
    }


def _rate(rows: list[dict], field: str) -> float | None:
    if not rows:
        return None
    return round(sum(1 for r in rows if r.get(field)) / len(rows), 3)


def run_traps(conn, traps: list[dict], engine_map: dict, *, cues: list[str]) -> dict:
    """Run every engine over every trap. Engines are callables taking a query and
    returning a normalized payload or None."""
    per_engine: dict[str, dict] = {}
    for name, run in engine_map.items():
        rows, latencies = [], []
        for trap in traps:
            started = time.perf_counter()
            try:
                out = run(trap["query"])
            except Exception as exc:  # noqa: BLE001
                log.warning("%s failed on %s: %s", name, trap["id"], exc)
                out = None
            latencies.append((time.perf_counter() - started) * 1000)
            rows.append(score_trap(out, trap, cues))
        summary = summarize(rows)
        summary["latency_ms"] = round(_avg(latencies) or 0, 1)
        per_engine[name] = {"summary": summary, "rows": rows}
    return {"n_traps": len(traps), "engines": per_engine}


MOO_ENGINES = ("moo_fast", "moo_deep")


def gate(result: dict, *, pass_bar: float = PASS_BAR,
         margin: float = COMPETITOR_MARGIN) -> dict:
    """Decide whether the USP survived. Two checks: moo clears the bar on its own,
    and it beats every competitor that ran by a wide margin. With no competitor
    keys the second check is reported as not evaluated rather than silently
    passing.

    There is a third outcome besides pass and fail. Every trap asks what the live
    web says today, so a run with no search provider retrieves nothing and scores
    0% — which is not the USP failing, it is the USP not being tested. That run
    returns ``status="not_evaluated"``: a launch gate that cannot tell "we broke
    it" from "we never ran it" is not a gate."""
    config = result.get("config") or {}
    if config and not config.get("live_provider"):
        return {
            "status": "not_evaluated",
            "passed": False,
            "pass_bar": pass_bar,
            "margin": margin,
            "checks": [{
                "name": "live retrieval",
                "passed": None,
                "detail": "no search provider configured, so nothing was fetched — "
                          "these traps ask what the live web says today, and a 0% here "
                          "means the run proves nothing either way",
            }],
        }

    engines = result.get("engines", {})
    moo = {name: data["summary"]["pass_rate"] for name, data in engines.items()
           if name in MOO_ENGINES and data["summary"]["pass_rate"] is not None}
    competitors = {name: data["summary"]["pass_rate"] for name, data in engines.items()
                   if name not in MOO_ENGINES and data["summary"]["pass_rate"] is not None}

    checks = []
    if not moo:
        checks.append({"name": "moo pass rate", "passed": False,
                       "detail": "no moo engine ran"})
        best_moo = None
    else:
        best_name = max(moo, key=lambda n: moo[n])
        best_moo = moo[best_name]
        checks.append({
            "name": "moo pass rate",
            "passed": best_moo >= pass_bar,
            "detail": f"best moo engine {best_name} flagged {best_moo:.0%} of traps "
                      f"(bar {pass_bar:.0%})",
        })

    if not competitors:
        checks.append({"name": "margin over competitors", "passed": None,
                       "detail": "no competitor keys set, head-to-head not evaluated"})
    else:
        best_competitor = max(competitors, key=lambda n: competitors[n])
        gap = (best_moo or 0) - competitors[best_competitor]
        checks.append({
            "name": "margin over competitors",
            "passed": gap >= margin,
            "detail": f"moo leads {best_competitor} by {gap:+.0%} "
                      f"(needs {margin:+.0%}); if a competitor flags staleness too, "
                      f"the USP wording has to change before launch",
        })

    passed = all(c["passed"] for c in checks if c["passed"] is not None)
    return {
        "status": "pass" if passed else "fail",
        "passed": passed,
        "pass_bar": pass_bar,
        "margin": margin,
        "checks": checks,
    }


def format_table(result: dict) -> str:
    header = (f"{'engine':<14}{'pass':>8}{'current':>9}{'flagged':>9}{'structural':>12}"
              f"{'silent stale':>14}{'latency ms':>12}{'errors':>8}")
    lines = [f"stale-answer traps  (n={result['n_traps']})", "", header, "-" * len(header)]
    for name, data in result["engines"].items():
        s = data["summary"]
        lines.append(
            f"{name:<14}{_pct(s['pass_rate']):>8}{_pct(s['current_rate']):>9}"
            f"{_pct(s['flag_rate']):>9}{_pct(s['structural_flag_rate']):>12}"
            f"{_pct(s['silent_stale_rate']):>14}{s['latency_ms']:>12}{s['errors']:>8}"
        )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.0%}"


_GATE_WORD = {"pass": "PASS", "fail": "FAIL", "not_evaluated": "NOT EVALUATED"}


def format_gate(verdict: dict) -> str:
    status = verdict.get("status") or ("pass" if verdict.get("passed") else "fail")
    lines = ["", "USP gate: " + _GATE_WORD.get(status, status.upper())]
    for check in verdict["checks"]:
        mark = {True: "pass", False: "FAIL", None: "n/a "}[check["passed"]]
        lines.append(f"  [{mark}] {check['name']}: {check['detail']}")
    return "\n".join(lines)


def format_transcript(result: dict, traps: list[dict], verdict: dict) -> str:
    """The per-trap record. A passing one is the launch demo; a failing one is the
    list of what to fix, which is the more useful state to be in early."""
    by_id = {t["id"]: t for t in traps}
    out = ["# Stale-answer traps", "",
           "Queries whose popular answer is out of date. An engine passes when it "
           "surfaces the current answer and marks the old one as outdated.", "",
           format_table(result), "```", format_gate(verdict).strip(), "```", ""]
    for trap_id, trap in by_id.items():
        out += [f"## {trap['query']}", "",
                f"**Changed in {trap['product']} {trap['changed_in']}** "
                f"({trap['change_type']}). Stale: {trap['stale']['summary']}. "
                f"Current: {trap['current']['summary']}.", ""]
        for name, data in result["engines"].items():
            row = next((r for r in data["rows"] if r["id"] == trap_id), None)
            if row is None:
                continue
            verdict_word = "pass" if row["passes"] else ("error" if row["error"] else "miss")
            bits = []
            if row["surfaces_current"]:
                bits.append("current answer present")
            if row["repeats_stale"]:
                bits.append("repeats the old answer")
            if row["flagged_structurally"]:
                signals = ", ".join(f"{k}={v}" for k, v in row["signals"].items() if v)
                bits.append(f"structural flag ({signals})")
            elif row["flags_stale"]:
                bits.append(f"textual flag ({', '.join(row['cues'])})")
            if row["silent_stale"]:
                bits.append("**no warning**")
            out.append(f"- **{name}**: {verdict_word} - {'; '.join(bits) or 'nothing matched'}")
            if row["quote"]:
                out.append(f"  > {row['quote']}")
        out.append("")
    return "\n".join(out)


def trap_engines(conn, *, k: int = 8, max_steps: int = 3, max_seconds: float = 90.0,
                 use_llm: bool = True, include: list[str] | None = None) -> dict:
    """moo fast and deep, plus every competitor whose key is set."""
    from .competitors import exa_deep_engine, exa_engine, tavily_engine, tavily_research_engine

    def moo_fast(query: str) -> dict:
        from ..websearch import web_search

        return web_search(conn, query, k=k, depth="raw", use_llm=False)

    def moo_deep(query: str) -> dict:
        from ..research.report import assemble_report
        from ..research.session import delete_run, run_research

        run = run_research(conn, query, k=k, max_steps=max_steps,
                           max_seconds=max_seconds, use_llm=use_llm)
        report = assemble_report(conn, run, use_llm=use_llm)
        delete_run(conn, run["run_id"])
        return report

    candidates = {
        "moo_fast": moo_fast,
        "moo_deep": moo_deep,
        "tavily": tavily_engine(k=k),
        "tavily_research": tavily_research_engine(),
        "exa": exa_engine(k=k),
        "exa_deep": exa_deep_engine(k=k),
    }
    chosen = {name: fn for name, fn in candidates.items() if fn is not None}
    if include:
        chosen = {name: fn for name, fn in chosen.items() if name in include}
    return chosen


def _config(engine_map: dict, *, use_llm: bool) -> dict:
    """What the numbers were produced under. A 0% with no search provider means
    nothing was retrieved, not that staleness detection failed, and a saved
    result that does not say so is worse than no result."""
    from .. import llm
    from ..live.providers import resolve_provider

    provider = resolve_provider()
    return {
        "engines": sorted(engine_map),
        "live_provider": type(provider).__name__ if provider is not None else None,
        "llm": use_llm and llm.get_client() is not None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.eval.stale_traps",
                                     description="USP acceptance gate: stale-answer traps")
    parser.add_argument("--engines", nargs="*", default=None,
                        help="subset to run: moo_fast moo_deep tavily tavily_research "
                             "exa exa_deep")
    parser.add_argument("--limit", type=int, default=None, help="run only the first N traps")
    parser.add_argument("-k", type=int, default=8)
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--max-seconds", type=float, default=90.0)
    parser.add_argument("--no-llm", action="store_true")
    parser.add_argument("--tasks", default=str(TASKS_PATH))
    parser.add_argument("--gate", action="store_true", help="exit 1 if the USP gate fails")
    parser.add_argument("--strict", action="store_true",
                        help="with --gate, also exit 1 when the gate could not be "
                             "evaluated (no search provider). Use this for the launch "
                             "check, where 'we never ran it' must not read as success")
    parser.add_argument("--save", default=None, help="write the full result JSON here")
    parser.add_argument("--transcript", default=None, help="write the markdown transcript here")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")

    from ..index.vector import connect

    data = load_traps(args.tasks)
    traps = data["traps"][:args.limit] if args.limit else data["traps"]
    conn = connect()
    try:
        engine_map = trap_engines(conn, k=args.k, max_steps=args.max_steps,
                                  max_seconds=args.max_seconds, use_llm=not args.no_llm,
                                  include=args.engines)
        result = run_traps(conn, traps, engine_map, cues=data["staleness_cues"])
    finally:
        conn.close()

    result["task_set_version"] = data.get("version")
    result["config"] = _config(engine_map, use_llm=not args.no_llm)
    verdict = gate(result)
    result["gate"] = verdict
    print(format_table(result))
    print(format_gate(verdict))

    if args.save:
        Path(args.save).write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
        print(f"\nsaved -> {args.save}")
    if args.transcript:
        Path(args.transcript).write_text(format_transcript(result, traps, verdict),
                                         encoding="utf-8")
        print(f"transcript -> {args.transcript}")

    if not args.gate:
        return 0
    status = verdict.get("status", "fail")
    if status == "fail":
        return 1
    if status == "not_evaluated":
        print("\nset MOO_SEARXNG_URL or MOO_BRAVE_API_KEY to actually test the USP")
        return 1 if args.strict else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

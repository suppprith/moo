"""Agent demo: drive moo's MCP tools end to end (SUP-123).

Runs the exact flow a coding agent follows — ``deep_research`` to get a grounded
cited report, then drill into the evidence with ``get_claim`` / ``fetch_source``,
then ``list_contradictions`` to see where sources disagree. Produces a
reproducible transcript (the "recording"): ``--save docs/agent-demo.md``.

Run:  uv run python -m app.eval.demo ["a research question"] [--save PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import textwrap

DEFAULT_QUESTION = "Postgres vs MySQL for complex analytical joins"


def _fill(text: str, width: int = 88, indent: str = "  ") -> str:
    return textwrap.fill(text, width, initial_indent=indent, subsequent_indent=indent)


async def run_demo(question: str, *, k: int = 5, max_steps: int = 4) -> str:
    """Execute the agent flow through moo's MCP tools and return a transcript."""
    from .. import mcp_server as mcp
    from ..index.vector import connect
    from ..retrieve import retrieve

    retrieve(connect(), "warm", k=2)  # warm the embedding model

    out: list[str] = ["# moo agent demo", "", f"**Question:** {question}", ""]

    out.append("## 1. deep_research — hand off the whole question")
    report = await mcp.deep_research(question, k=k, max_steps=max_steps, max_seconds=120, ctx=None)
    g = report["groundedness"]
    out += [
        "",
        f"`run_id={report['run_id'][:12]}` · status **{report['status']}** · "
        f"grounded **{g['findings_grounded']}/{g['findings_total']}** "
        f"({g['well_supported']} well-supported) · answer citations valid: {g['answer_citations_valid']}",
        "",
        "**Executive answer**", "", _fill(report["executive_answer"] or "(none)"), "",
        "**Findings**",
    ]
    for f in report["findings"][:5]:
        tag = "disputed" if f["disputed"] else f"conf {f['confidence']}"
        out.append(f"- _({tag})_ {f['text'][:100]}  -> cites {f['citations']}  `{f['claim']}`")

    if report["disputed_points"]:
        dp = report["disputed_points"][0]
        out += [
            "", "**Disputed point (moo surfaces disagreement instead of averaging it):**",
            f"- {dp['text'][:120]}", f"  - supports S{dp['supports']} · contradicts S{dp['contradicts']}",
        ]
    if report["open_questions"]:
        out += ["", "**Open questions (what it could not cover):**"]
        out += [f"- {q}" for q in report["open_questions"][:3]]

    # drill into the evidence, the way an agent verifies a citation
    if report["findings"]:
        handle = report["findings"][0]["claim"]
        out += ["", f"## 2. get_claim({handle!r}) — verify a finding's evidence"]
        claim = mcp.get_claim(handle)
        out += [
            "", f"confidence {claim['confidence']} · disputed {claim['disputed']} · "
            f"{len(claim['evidence'])} evidence edges",
        ]
        for e in claim["evidence"][:3]:
            out.append(f"- {e['relation']} `{e['source']}` (trust {e.get('trust_score')}): "
                       f"{(e.get('excerpt') or '')[:80]}")

    if report["sources"]:
        h = report["sources"][0]["handle"]
        out += ["", f"## 3. fetch_source({h!r}) — read the source"]
        src = mcp.fetch_source(h, max_tokens=180)
        out += ["", f"- {(src.get('title') or '(untitled)')[:70]} — {src.get('url', '')[:70]}",
                f"  trust_tier via report · {len(src.get('chunks', []))} chunks"]

    out += ["", f"## 4. list_contradictions({question!r})"]
    contra = mcp.list_contradictions(question, k=6)
    out += ["", f"- disputes surfaced: **{len(contra['contradictions'])}**"]
    for c in contra["contradictions"][:3]:
        out.append(f"  - {c['text'][:90]}")

    out += ["", "---", "_Reproduce: `uv run python -m app.eval.demo`_"]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.eval.demo", description="moo agent demo")
    parser.add_argument("question", nargs="?", default=DEFAULT_QUESTION)
    parser.add_argument("--max-steps", type=int, default=4)
    parser.add_argument("--save", default=None, help="write the transcript to this path")
    args = parser.parse_args(argv)

    transcript = asyncio.run(run_demo(args.question, max_steps=args.max_steps))
    if args.save:
        from pathlib import Path

        Path(args.save).write_text(transcript + "\n", encoding="utf-8")
    try:  # Windows consoles are cp1252; corpus text may contain other glyphs
        import sys

        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    print(transcript)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

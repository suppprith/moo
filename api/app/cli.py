"""``moo`` — search the web from your terminal (SUP-102).

    moo "why do my containers randomly exit"

Prints a cited answer as markdown (mode ``full``), or ranked sources
(``--mode raw``) / claims with confidence (``--mode claims``). Flags:

- ``--json``       emit the full response for piping into ``jq``
- ``--open N``     open the Nth source in your browser
- ``--url URL``    talk to a running moo instance over HTTP (or set
                   ``MOO_URL``); without it the engine runs **in-process** —
                   no server needed, self-initializing on first run
- ``-k``, ``--no-live``, ``--mode`` mirror the /search contract

Pipe-friendly: ANSI colors only when stdout is a TTY; ``--json`` is raw JSON.
Neither Google nor DuckDuckGo ships an official CLI — moo does.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import webbrowser


# -- rendering (pure: testable without a corpus) --------------------------------

def _c(code: str, s: str, color: bool) -> str:
    return f"\x1b[{code}m{s}\x1b[0m" if color else s


def _confidence_tag(claim: dict, color: bool) -> str:
    conf = claim.get("confidence")
    tag = f"{round(conf * 100)}%" if conf is not None else "?"
    if claim.get("superseded_by"):
        return _c("90", f"[superseded]", color)
    if claim.get("disputed"):
        return _c("33", f"[disputed {tag}]", color)
    return _c("32", f"[{tag}]", color)


def render(response: dict, *, color: bool = False) -> str:
    """Terminal rendering of a /search response (any mode)."""
    lines: list[str] = []
    answer = response.get("answer")
    if answer:
        lines.append(answer.strip())
        lines.append("")

    claims = response.get("claims") or []
    if claims and not answer:
        for cl in claims:
            lines.append(f"- {cl['text']} {_confidence_tag(cl, color)}")
        lines.append("")

    citations = response.get("citations") or []
    sources = response.get("sources") or []
    if citations:
        lines.append(_c("1", "Sources", color))
        for c in citations:
            lines.append(f"  [S{c.get('index', '?')}] {c.get('url', '')}")
    elif sources:
        lines.append(_c("1", "Sources", color))
        for i, s in enumerate(sources, 1):
            url = s.get("url_anchor") or s.get("document_url") or s.get("url", "")
            title = s.get("title") or url
            marks = ""
            if s.get("suspicious"):
                marks += _c("31", " ⚠ untrusted", color)
            if s.get("version_outdated"):
                marks += _c("33", " (older version)", color)
            lines.append(f"  {i:>2}. {_c('1', title, color)}{marks}")
            lines.append(f"      {url}")

    meta = response.get("meta") or {}
    live = meta.get("live")
    if live:
        note = f"live: {live.get('fetched', 0)} fetched, {live.get('fresh', 0)} cached"
        if live.get("out_of_domain"):
            note = "query looks out of moo's software domain — results may be thin"
        lines.append("")
        lines.append(_c("90", note, color))
    if not sources and not claims and not answer:
        lines.append("no results")
    return "\n".join(lines).rstrip() + "\n"


def _source_urls(response: dict) -> list[str]:
    out = []
    for s in response.get("sources") or []:
        out.append(s.get("url_anchor") or s.get("document_url") or s.get("url", ""))
    return out


# -- backends --------------------------------------------------------------------

def _search_http(base_url: str, args) -> dict:
    import httpx

    params = {"q": args.query, "mode": args.mode, "k": args.k}
    if args.no_live:
        params["live"] = "false"
    resp = httpx.get(f"{base_url.rstrip('/')}/search", params=params, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _search_inprocess(args) -> dict:
    from .bootstrap import ensure_ready
    from .index.vector import connect
    from .search import search

    ensure_ready(quiet=True)
    conn = connect()
    try:
        return search(conn, args.query, mode=args.mode, k=args.k,
                      live=False if args.no_live else None)
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="moo", description="Live web search for software questions, with receipts."
    )
    parser.add_argument("query", help="what you want to know")
    parser.add_argument("--mode", choices=("raw", "claims", "full"), default="full",
                        help="raw=sources only (fast), claims=+evidence, full=cited answer (default)")
    parser.add_argument("-k", type=int, default=8, help="number of sources")
    parser.add_argument("--json", action="store_true", help="emit the full JSON response")
    parser.add_argument("--open", type=int, metavar="N", help="open the Nth source in a browser")
    parser.add_argument("--no-live", action="store_true", help="local store only (skip live fetch)")
    parser.add_argument("--url", default=os.environ.get("MOO_URL"),
                        help="running moo instance to query (default: run in-process)")
    args = parser.parse_args(argv)

    if sys.platform == "win32":  # corpus text can exceed cp1252
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:  # noqa: BLE001 - cosmetic only
            pass

    try:
        response = _search_http(args.url, args) if args.url else _search_inprocess(args)
    except Exception as exc:  # noqa: BLE001 - a CLI should fail with one clear line
        print(f"moo: search failed: {exc}", file=sys.stderr)
        if args.url:
            print(f"moo: is a moo instance running at {args.url}?", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(response, indent=2, default=str))
        return 0

    color = sys.stdout.isatty()  # pipe-friendly: no ANSI junk in scripts
    sys.stdout.write(render(response, color=color))

    if args.open:
        urls = _source_urls(response)
        if 1 <= args.open <= len(urls) and urls[args.open - 1]:
            webbrowser.open(urls[args.open - 1])
        else:
            print(f"moo: no source #{args.open} to open", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

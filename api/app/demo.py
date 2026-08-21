"""``moo demo`` — a working store and one query end to end, from a fresh clone.

The problem this solves: moo with an empty store answers nothing, and filling it
properly means a search provider key and a crawl. That is a fine setup for an
operator and a terrible first five minutes for someone deciding whether the
project is worth their time.

So the demo seeds a small corpus by fetching a **committed list of primary
sources** — the pages themselves, directly, no discovery provider and no API key
of any kind — then runs one query through the full pipeline and narrates what
came back. Fetching at run time rather than shipping a database is deliberate:
the corpus is other people's writing under their own licences, and the honest
way to hold it is the way normal use holds it, in a local cache the reader
fetched themselves.

The default question is one no single page answers — SQLite versus Postgres —
so the walkthrough has to pull from several sources and cite which sentence came
from where, which is the part worth looking at. The corpus also carries a page
whose popular answer is stale (`datetime.utcnow()`, deprecated since Python
3.12), for `--query "how do I get the current UTC time in Python"`.

The walkthrough reports whether moo flagged anything as disputed or superseded,
**including when it flagged nothing**. On thirteen pages answering keyless it
usually does flag nothing, and saying so is the point: a demo that can only pass
is a screenshot, not a test.

    uv run moo demo                # seed if needed, then the walkthrough
    uv run moo demo --seed-only    # just fill the store
    uv run moo demo --query "..."  # your own question against the demo corpus
"""

from __future__ import annotations

import argparse
import logging
import sqlite3
import time

# (url, why this page is in the corpus). Primary sources only: specs, official
# docs and release notes — the things moo is meant to be good at finding.
DEMO_SOURCES: tuple[tuple[str, str], ...] = (
    ("https://docs.python.org/3/library/datetime.html",
     "says utcnow() is deprecated — the current answer to the default query"),
    ("https://www.sqlite.org/whentouse.html", "when SQLite is and isn't the right choice"),
    ("https://www.sqlite.org/wal.html", "WAL mode, concurrency and checkpointing"),
    ("https://www.postgresql.org/docs/current/routine-vacuuming.html",
     "autovacuum: defaults and what it reclaims"),
    ("https://www.postgresql.org/docs/current/wal-intro.html", "Postgres write-ahead logging"),
    ("https://redis.io/docs/latest/operate/oss_and_stack/management/persistence/",
     "RDB vs AOF — two options the docs weigh against each other"),
    ("https://nodejs.org/api/esm.html", "ESM and CommonJS interop"),
    ("https://doc.rust-lang.org/book/ch15-04-rc.html", "Rc, and why it is not Arc"),
    ("https://react.dev/reference/react/useMemo", "when memoizing is worth it"),
    ("https://git-scm.com/docs/git-rebase", "rebase, including the warnings about it"),
    ("https://docs.docker.com/build/building/best-practices/", "image size and layer advice"),
    ("https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/",
     "liveness vs readiness, a distinction that is constantly got wrong"),
    ("https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Cache-Control",
     "Cache-Control semantics"),
)

DEMO_QUERY = "when should I use SQLite instead of Postgres"
STALE_QUERY = "how do I get the current UTC time in Python"

BATCH = 10  # extract_urls caps a call at MAX_URLS


def stored_urls(conn: sqlite3.Connection, urls: list[str]) -> set[str]:
    if not urls:
        return set()
    marks = ",".join("?" * len(urls))
    return {
        row[0] for row in conn.execute(f"SELECT url FROM document WHERE url IN ({marks})", urls)
    }


def seed(conn: sqlite3.Connection, *, force: bool = False, use_llm: bool = True,
         echo=print) -> dict:
    """Fetch the demo corpus into the store. Already-stored pages are skipped
    unless ``force``, so a second run costs nothing."""
    from .extract import extract_urls

    urls = [url for url, _ in DEMO_SOURCES]
    have = set() if force else stored_urls(conn, urls)
    todo = [url for url in urls if url not in have]
    if have:
        echo(f"  {len(have)} page(s) already in the store")
    if not todo:
        return {"fetched": 0, "skipped": len(have), "failed": [], "seconds": 0.0}

    started = time.perf_counter()
    fetched, failed = 0, []
    for start in range(0, len(todo), BATCH):
        batch = todo[start:start + BATCH]
        echo(f"  fetching {len(batch)} page(s)...")
        out = extract_urls(conn, batch, depth="claims", use_llm=use_llm, force=force)
        for entry in out["results"]:
            if entry.get("ok", True) and not entry.get("error"):
                fetched += 1
            else:
                failed.append(entry.get("url", "?"))
    return {
        "fetched": fetched,
        "skipped": len(have),
        "failed": failed,
        "seconds": round(time.perf_counter() - started, 1),
    }


def walkthrough(conn: sqlite3.Connection, query: str, *, k: int = 6,
                use_llm: bool = True) -> dict:
    """Run one query the way an agent would and collect what came back."""
    from .search import search

    started = time.perf_counter()
    result = search(conn, query, mode="full", k=k, use_llm=use_llm, live=False)
    result["_seconds"] = round(time.perf_counter() - started, 1)
    return result


def _staleness_note(result: dict) -> str:
    """Whether this run actually showed the thing moo claims to do."""
    claims = result.get("claims") or []
    disputed = [c for c in claims if c.get("disputed")]
    superseded = [c for c in claims if c.get("superseded_by")]
    outdated = [s for s in result.get("sources") or [] if s.get("version_outdated")]

    if disputed or superseded or outdated:
        parts = []
        if disputed:
            parts.append(f"{len(disputed)} disputed claim(s)")
        if superseded:
            parts.append(f"{len(superseded)} superseded claim(s)")
        if outdated:
            parts.append(f"{len(outdated)} source(s) flagged as an older version")
        return "moo flagged: " + ", ".join(parts)
    return (
        "Nothing was flagged as disputed or superseded on this run. That is the "
        "honest result for a small store answering keyless: flagging needs two "
        "independent sources on the same point, and a demo corpus this small "
        "usually has one. Add a search provider (moo setup) and the live path "
        "fetches enough sources for the evidence layer to have something to "
        "disagree about."
    )


def _one_row_per_document(sources: list[dict]) -> list[dict]:
    """Six anchors into one page is one source, and listing it six times reads
    as breadth that isn't there. Keep each document's best-ranked chunk."""
    seen: set[str] = set()
    out = []
    for source in sources:
        key = source.get("document_url") or source.get("url_anchor") or ""
        if key in seen:
            continue
        seen.add(key)
        out.append(source)
    return out


def render(query: str, result: dict) -> str:
    lines = [f'  query: "{query}"  ({result["_seconds"]}s)', ""]

    answer = result.get("answer")
    if answer:
        lines += ["  " + answer.strip().replace("\n", "\n  "), ""]

    claims = result.get("claims") or []
    if claims:
        lines.append("  Claims, each with its confidence and its evidence:")
        for claim in claims[:5]:
            conf = claim.get("confidence")
            tag = f"{round(conf * 100)}%" if conf is not None else "?"
            mark = " [disputed]" if claim.get("disputed") else ""
            text = claim["text"].strip()
            lines.append(f"    - ({tag}){mark} {text[:150]}")
        lines.append("")

    sources = _one_row_per_document(result.get("sources") or [])
    if sources:
        lines.append("  Sources:")
        for n, source in enumerate(sources[:6], 1):
            url = source.get("url_anchor") or source.get("document_url") or ""
            trust = source.get("trust_score")
            trust_s = f"trust {trust:.2f}" if isinstance(trust, (int, float)) else "trust -"
            lines.append(f"    {n}. {(source.get('title') or url)[:70]}  ({trust_s})")
            lines.append(f"       {url}")
        lines.append("")

    lines += ["  " + _staleness_note(result), ""]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="moo demo", description="Seed a small corpus and run one query end to end."
    )
    parser.add_argument("--query", default=DEMO_QUERY, help="ask something else instead")
    parser.add_argument("--seed-only", action="store_true", help="fill the store and stop")
    parser.add_argument("--force", action="store_true", help="re-fetch pages already stored")
    parser.add_argument("--no-llm", action="store_true", help="force the heuristic path")
    parser.add_argument("-k", type=int, default=6, help="how many sources to retrieve")
    args = parser.parse_args(argv)

    from .bootstrap import ensure_ready
    from .cli import use_utf8_stdout
    from .index.vector import connect

    # Stage-timing warnings are useful in an operator's logs and noise in a
    # walkthrough someone is reading for the first time.
    logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(name)s %(message)s")
    use_utf8_stdout()
    started = time.perf_counter()

    print("moo demo\n")
    print("1. Preparing the store (created and migrated on first use)")
    ensure_ready(quiet=True)
    conn = connect()
    try:
        print(f"\n2. Seeding {len(DEMO_SOURCES)} primary sources — fetched directly, no API key")
        report = seed(conn, force=args.force, use_llm=not args.no_llm)
        print(f"  fetched {report['fetched']}, skipped {report['skipped']}, "
              f"failed {len(report['failed'])} in {report['seconds']}s")
        for url in report["failed"]:
            print(f"    could not fetch: {url}")
        if report["fetched"] == 0 and report["skipped"] == 0:
            print("\nNothing was fetched, so there is nothing to search. "
                  "Check the network and try again.")
            return 1
        if args.seed_only:
            print(f"\nStore ready in {round(time.perf_counter() - started, 1)}s.")
            return 0

        print("\n3. One query, through the full pipeline\n")
        print(render(args.query, walkthrough(conn, args.query, k=args.k,
                                             use_llm=not args.no_llm)))
    finally:
        conn.close()

    print(f"Done in {round(time.perf_counter() - started, 1)}s. Next:")
    print('  uv run moo "why is my postgres query slow"     ask your own')
    print("  uv run moo setup --print-config                connect your agent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

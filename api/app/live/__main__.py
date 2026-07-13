"""CLI for live retrieval:  ``uv run python -m app.live "<query>" [-k N]``.

Runs discover -> fetch -> ingest, then a hybrid retrieval over the refreshed
store, and prints both the live report and the top hits.
"""

from __future__ import annotations

import argparse
import json
import logging

from ..db import migrate
from ..index import vector
from ..retrieve import retrieve
from .pipeline import live_fetch


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.live", description="Live discover+fetch retrieval")
    parser.add_argument("query")
    parser.add_argument("-k", type=int, default=8)
    parser.add_argument("--max-pages", type=int, default=6)
    parser.add_argument("--json", action="store_true", help="emit the full report as JSON")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    migrate()
    conn = vector.connect()
    report = live_fetch(conn, args.query, max_pages=args.max_pages)

    if not report["available"]:
        print(
            "live search not configured — set MOO_SEARXNG_URL (keyless, self-hosted)\n"
            "or MOO_BRAVE_API_KEY; falling back to the local store."
        )
    elif report["out_of_domain"]:
        print(
            f"query looks out of domain (confidence "
            f"{report['domain_confidence']}) — moo answers software questions; "
            "searching the local store only."
        )

    hits = retrieve(conn, args.query, k=args.k)
    if args.json:
        print(json.dumps({"live": report, "hits": [h.__dict__ for h in hits]},
                         indent=2, default=str))
    else:
        if report["available"]:
            print(
                f"[live] provider={report['provider']} discovered={report['discovered']} "
                f"fetched={report['fetched']} unchanged={report['unchanged']} "
                f"failed={report['failed']} new_chunks={report['new_chunks']} "
                f"timings={report['timings_ms']}"
            )
        for h in hits:
            print(f"  #{h.chunk_id:<6} {h.source_type:14} {h.document_url}")
            print(f"     {h.text[:100].strip()!r}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

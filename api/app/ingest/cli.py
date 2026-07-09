"""Ingestion CLI:  ``uv run python -m app.ingest <connector> [options]``.

Examples:
    uv run python -m app.ingest github --repo redis/redis --limit 20
    uv run python -m app.ingest docs
"""

from __future__ import annotations

import argparse
import logging

from ..db import get_connection, migrate
from .github import GitHubConnector

CONNECTORS = {
    "github": GitHubConnector,
}


def _build(name: str, conn, args) -> object:
    fetcher = CONNECTORS[name].default_fetcher()
    if name == "github":
        repos = [args.repo] if args.repo else None
        return GitHubConnector(conn, fetcher, repos=repos, max_issues=args.max_per_repo)
    return CONNECTORS[name](conn, fetcher)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.ingest", description="Run a moo source connector")
    parser.add_argument("connector", choices=sorted(CONNECTORS))
    parser.add_argument("--repo", help="github: single owner/repo instead of all seeds")
    parser.add_argument("--limit", type=int, default=None, help="max documents to store")
    parser.add_argument("--max-per-repo", type=int, default=30, help="github: items per repo")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    migrate()
    conn = get_connection()
    connector = _build(args.connector, conn, args)
    stats = connector.run(limit=args.limit)
    print(f"[{args.connector}] {stats}")
    if stats.error_urls:
        print("errors:", *stats.error_urls[:10], sep="\n  ")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

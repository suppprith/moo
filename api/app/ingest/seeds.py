"""Seed sources for the v1 databases vertical (see docs/v1-vertical.md).

Kept as plain data so connectors and the eval share one source of truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# GitHub repos whose issues / PRs / releases carry the "dark knowledge".
GITHUB_REPOS: list[str] = [
    "postgres/postgres",
    "redis/redis",
    "sqlite/sqlite",
    "MariaDB/server",
    "pgbouncer/pgbouncer",
    "prisma/prisma",
    "sqlalchemy/sqlalchemy",
]


@dataclass
class DocsSite:
    name: str
    source_type: str            # docs | blog
    sitemap: str | None = None  # sitemap.xml to discover pages from
    pages: list[str] = field(default_factory=list)  # explicit seed pages
    allow_prefix: str | None = None  # only keep sitemap URLs under this prefix


# Official docs + engineering blogs. Sitemaps are capped by the connector so a
# full site is not crawled in one shot.
DOCS_SITES: list[DocsSite] = [
    DocsSite(
        name="SQLite docs",
        source_type="docs",
        pages=[
            "https://www.sqlite.org/whentouse.html",
            "https://www.sqlite.org/wal.html",
            "https://www.sqlite.org/datatype3.html",
        ],
    ),
    DocsSite(
        name="PostgreSQL wiki — Don't Do This",
        source_type="docs",
        pages=["https://wiki.postgresql.org/wiki/Don%27t_Do_This"],
    ),
    DocsSite(
        name="Use The Index, Luke!",
        source_type="blog",
        pages=[
            "https://use-the-index-luke.com/sql/where-clause/the-equals-operator",
            "https://use-the-index-luke.com/sql/anatomy/the-leaf-nodes",
        ],
    ),
]

# Stack Overflow tags / subreddits / HN queries for the community connector (SUP-75).
STACKOVERFLOW_TAGS = ["postgresql", "mysql", "sqlite", "redis", "database-indexing"]
SUBREDDITS = ["PostgreSQL", "Database", "redis"]
HN_QUERIES = [
    "postgresql performance",
    "mysql vs postgres",
    "redis persistence",
    "sqlite production",
]

# Used to keep off-topic HN/Reddit hits out of the corpus.
DOMAIN_KEYWORDS = [
    "postgres", "postgresql", "mysql", "mariadb", "sqlite", "redis",
    "database", "index", "query", "sql", "transaction", "deadlock",
    "vacuum", "replication", "b-tree", "jsonb", "wal",
]


def is_domain_relevant(*texts: str | None) -> bool:
    """True if any db keyword appears in the given texts (case-insensitive)."""
    blob = " ".join(t for t in texts if t).lower()
    return any(kw in blob for kw in DOMAIN_KEYWORDS)

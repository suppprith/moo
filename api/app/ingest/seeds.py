"""Seed sources, organized by CS/coding vertical (SUP-125).

v1 goes deep on **databases**; further verticals (languages, web frameworks,
build tooling, cloud/infra, systems) start shallower and deepen over time — the
gate to moo being a coding agent's *default* web_search. Everything is plain data
so connectors and the eval share one source of truth.

Connectors ingest all verticals by default (the module-level ``GITHUB_REPOS`` /
``DOCS_SITES`` / ``STACKOVERFLOW_TAGS`` / … are unions). ``is_domain_relevant``
spans every vertical's keywords, so ingestion + retrieval cross domains while
off-topic (non-CS) noise is still filtered out. Ingest one vertical at a time
with ``uv run python -m app.ingest <connector> --vertical <name>``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DocsSite:
    name: str
    source_type: str            # docs | blog
    sitemap: str | None = None  # sitemap.xml to discover pages from
    pages: list[str] = field(default_factory=list)  # explicit seed pages
    allow_prefix: str | None = None  # only keep sitemap URLs under this prefix


@dataclass
class Vertical:
    name: str
    github_repos: list[str] = field(default_factory=list)
    docs_sites: list[DocsSite] = field(default_factory=list)
    stackoverflow_tags: list[str] = field(default_factory=list)
    subreddits: list[str] = field(default_factory=list)
    hn_queries: list[str] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)


VERTICALS: dict[str, Vertical] = {
    # -- v1: deep end-to-end proof -------------------------------------------
    "databases": Vertical(
        name="databases",
        github_repos=[
            "postgres/postgres", "redis/redis", "sqlite/sqlite", "MariaDB/server",
            "pgbouncer/pgbouncer", "prisma/prisma", "sqlalchemy/sqlalchemy",
        ],
        docs_sites=[
            DocsSite("SQLite docs", "docs", pages=[
                "https://www.sqlite.org/whentouse.html",
                "https://www.sqlite.org/wal.html",
                "https://www.sqlite.org/datatype3.html",
            ]),
            DocsSite("PostgreSQL wiki — Don't Do This", "docs",
                     pages=["https://wiki.postgresql.org/wiki/Don%27t_Do_This"]),
            DocsSite("Use The Index, Luke!", "blog", pages=[
                "https://use-the-index-luke.com/sql/where-clause/the-equals-operator",
                "https://use-the-index-luke.com/sql/anatomy/the-leaf-nodes",
            ]),
        ],
        stackoverflow_tags=["postgresql", "mysql", "sqlite", "redis", "database-indexing"],
        subreddits=["PostgreSQL", "Database", "redis"],
        hn_queries=["postgresql performance", "mysql vs postgres", "redis persistence",
                    "sqlite production"],
        keywords=[
            "postgres", "postgresql", "mysql", "mariadb", "sqlite", "redis",
            "database", "index", "query", "sql", "transaction", "deadlock",
            "vacuum", "replication", "b-tree", "jsonb", "wal",
        ],
    ),
    # -- shallow-start verticals ---------------------------------------------
    "languages": Vertical(
        name="languages",
        github_repos=["python/cpython", "nodejs/node"],
        docs_sites=[
            DocsSite("Python docs", "docs", pages=[
                "https://docs.python.org/3/glossary.html",
                "https://docs.python.org/3/library/asyncio-task.html",
            ]),
        ],
        stackoverflow_tags=["python", "node.js", "python-asyncio"],
        subreddits=["Python", "node"],
        hn_queries=["python gil", "node event loop"],
        keywords=["python", "cpython", "gil", "asyncio", "coroutine", "node", "nodejs",
                  "javascript", "event loop", "garbage collection", "interpreter", "v8"],
    ),
    "web-frameworks": Vertical(
        name="web-frameworks",
        github_repos=["fastapi/fastapi", "django/django"],
        docs_sites=[
            DocsSite("FastAPI docs", "docs", pages=[
                "https://fastapi.tiangolo.com/async/",
                "https://fastapi.tiangolo.com/tutorial/first-steps/",
            ]),
        ],
        stackoverflow_tags=["fastapi", "django", "flask"],
        subreddits=["django"],
        hn_queries=["fastapi async", "django orm"],
        keywords=["fastapi", "django", "flask", "starlette", "asgi", "wsgi", "orm",
                  "middleware", "pydantic", "web framework", "rest api"],
    ),
    "build-tooling": Vertical(
        name="build-tooling",
        github_repos=["astral-sh/uv", "pypa/pip"],
        docs_sites=[
            DocsSite("uv docs", "docs", pages=[
                "https://docs.astral.sh/uv/concepts/projects/layout/",
            ]),
        ],
        stackoverflow_tags=["pip", "python-poetry", "npm"],
        subreddits=[],
        hn_queries=["python packaging", "uv package manager"],
        keywords=["uv", "pip", "poetry", "pipenv", "virtualenv", "packaging", "wheel",
                  "npm", "yarn", "pnpm", "bundler", "webpack", "vite", "dependency resolution"],
    ),
    "cloud-infra": Vertical(
        name="cloud-infra",
        github_repos=["kubernetes/kubernetes", "hashicorp/terraform"],
        docs_sites=[
            DocsSite("Kubernetes docs", "docs", pages=[
                "https://kubernetes.io/docs/concepts/overview/",
            ]),
        ],
        stackoverflow_tags=["kubernetes", "docker", "terraform"],
        subreddits=["kubernetes", "devops"],
        hn_queries=["kubernetes networking", "terraform state"],
        keywords=["kubernetes", "k8s", "docker", "container", "pod", "helm", "terraform",
                  "infrastructure as code", "aws", "gcp", "cloud native", "ingress"],
    ),
    "systems": Vertical(
        name="systems",
        github_repos=[],  # kernel-scale repos are out of scope; docs/community only
        docs_sites=[],
        stackoverflow_tags=["linux", "operating-system", "memory-management"],
        subreddits=[],
        hn_queries=["linux memory management", "how syscalls work"],
        keywords=["linux", "kernel", "syscall", "system call", "process", "thread",
                  "mmap", "memory management", "filesystem", "scheduler", "virtual memory"],
    ),
}


def list_verticals() -> list[str]:
    return list(VERTICALS)


def get_vertical(name: str) -> Vertical:
    if name not in VERTICALS:
        raise KeyError(f"unknown vertical {name!r}; known: {list_verticals()}")
    return VERTICALS[name]


def _union(attr: str) -> list:
    out: list = []
    for v in VERTICALS.values():
        for item in getattr(v, attr):
            if item not in out:
                out.append(item)
    return out


# Module-level unions across all verticals (connectors default to these).
GITHUB_REPOS: list[str] = _union("github_repos")
DOCS_SITES: list[DocsSite] = [d for v in VERTICALS.values() for d in v.docs_sites]
STACKOVERFLOW_TAGS: list[str] = _union("stackoverflow_tags")
SUBREDDITS: list[str] = _union("subreddits")
HN_QUERIES: list[str] = _union("hn_queries")
DOMAIN_KEYWORDS: list[str] = _union("keywords")


def is_domain_relevant(*texts: str | None) -> bool:
    """True if any CS/coding keyword (across all verticals) appears in the texts.
    Keeps off-topic community noise out while spanning every vertical."""
    blob = " ".join(t for t in texts if t).lower()
    return any(kw in blob for kw in DOMAIN_KEYWORDS)

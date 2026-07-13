"""Software-domain source policy (SUP-130).

moo is scoped to software/CS — all of it, not one vertical, and not the general
web. Under live retrieval that scope is enforced here rather than by what was
pre-indexed: discovered URLs get a **prior** from their domain (official docs
highest, community Q&A high, unknown low, junk blocked), candidates are ranked
prior-first, and a query whose discovery results barely touch the dev-source
universe is flagged **out of domain** instead of being answered badly.

The table is a *soft* whitelist: unknown domains still surface (rank lower
until trusted), only actively unfetchable/off-topic hosts are blocked.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from .providers import Candidate

# tier -> prior. Priors feed candidate ranking only; document trust scoring
# (app/evidence/trust.py) stays the authority once content is fetched.
TIER_PRIORS = {
    "docs": 1.0,       # official documentation / reference
    "repo": 0.85,      # source forges (issues, PRs, READMEs)
    "registry": 0.8,   # package registries
    "qa": 0.8,         # community Q&A
    "blog": 0.55,      # engineering blogs / dev platforms
    "unknown": 0.35,   # never seen it — surfaces, ranked low
}

# A query is out-of-domain when the mean prior of its top candidates is below
# this: software queries land mostly on tiered dev domains (mean >= ~0.6),
# recipe/news/shopping queries land almost entirely on unknowns (~0.35).
OFF_DOMAIN_THRESHOLD = 0.5

# host suffix -> (tier, source_type). Matched longest-suffix-first, so
# "docs.python.org" wins over a hypothetical "python.org" entry.
_HOSTS: dict[str, tuple[str, str]] = {
    # -- official docs / reference ------------------------------------------
    "docs.python.org": ("docs", "docs"),
    "developer.mozilla.org": ("docs", "docs"),
    "learn.microsoft.com": ("docs", "docs"),
    "docs.oracle.com": ("docs", "docs"),
    "docs.aws.amazon.com": ("docs", "docs"),
    "cloud.google.com": ("docs", "docs"),
    "kubernetes.io": ("docs", "docs"),
    "docs.docker.com": ("docs", "docs"),
    "developer.hashicorp.com": ("docs", "docs"),
    "postgresql.org": ("docs", "docs"),
    "dev.mysql.com": ("docs", "docs"),
    "mariadb.com": ("docs", "docs"),
    "sqlite.org": ("docs", "docs"),
    "redis.io": ("docs", "docs"),
    "mongodb.com": ("docs", "docs"),
    "docs.djangoproject.com": ("docs", "docs"),
    "fastapi.tiangolo.com": ("docs", "docs"),
    "flask.palletsprojects.com": ("docs", "docs"),
    "nodejs.org": ("docs", "docs"),
    "react.dev": ("docs", "docs"),
    "vuejs.org": ("docs", "docs"),
    "angular.dev": ("docs", "docs"),
    "go.dev": ("docs", "docs"),
    "pkg.go.dev": ("docs", "docs"),
    "doc.rust-lang.org": ("docs", "docs"),
    "docs.rs": ("docs", "docs"),
    "rust-lang.org": ("docs", "docs"),
    "cppreference.com": ("docs", "docs"),
    "man7.org": ("docs", "docs"),
    "git-scm.com": ("docs", "docs"),
    "docs.github.com": ("docs", "docs"),
    "docs.gitlab.com": ("docs", "docs"),
    "docs.astral.sh": ("docs", "docs"),
    "pip.pypa.io": ("docs", "docs"),
    "packaging.python.org": ("docs", "docs"),
    "readthedocs.io": ("docs", "docs"),          # *.readthedocs.io
    "kernel.org": ("docs", "docs"),
    "wiki.postgresql.org": ("docs", "docs"),
    # -- source forges --------------------------------------------------------
    "github.com": ("repo", "docs"),              # refined by path below
    "gitlab.com": ("repo", "docs"),
    "bitbucket.org": ("repo", "docs"),
    # -- package registries ---------------------------------------------------
    "pypi.org": ("registry", "docs"),
    "npmjs.com": ("registry", "docs"),
    "crates.io": ("registry", "docs"),
    "rubygems.org": ("registry", "docs"),
    "nuget.org": ("registry", "docs"),
    "hex.pm": ("registry", "docs"),
    "mvnrepository.com": ("registry", "docs"),
    # -- community Q&A --------------------------------------------------------
    "stackoverflow.com": ("qa", "so"),
    "stackexchange.com": ("qa", "so"),           # *.stackexchange.com
    "serverfault.com": ("qa", "so"),
    "superuser.com": ("qa", "so"),
    "askubuntu.com": ("qa", "so"),
    "news.ycombinator.com": ("qa", "hn"),
    "reddit.com": ("qa", "reddit"),
    # -- dev blogs / platforms ------------------------------------------------
    "dev.to": ("blog", "blog"),
    "medium.com": ("blog", "blog"),
    "hashnode.dev": ("blog", "blog"),
    "substack.com": ("blog", "blog"),
    "github.io": ("blog", "blog"),               # *.github.io project pages
    "martinfowler.com": ("blog", "blog"),
    "use-the-index-luke.com": ("blog", "blog"),
    "baeldung.com": ("blog", "blog"),
    "digitalocean.com": ("blog", "blog"),        # community tutorials
    "geeksforgeeks.org": ("blog", "blog"),
    "w3schools.com": ("blog", "blog"),
    "realpython.com": ("blog", "blog"),
}

# Unfetchable or never-software hosts. Kept deliberately short — the soft
# prior does the real filtering; blocking is only for pages we can't extract
# (video, walled social) at all.
_BLOCKED = {
    "youtube.com", "youtu.be", "vimeo.com",
    "facebook.com", "instagram.com", "tiktok.com", "pinterest.com",
    "x.com", "twitter.com", "linkedin.com",
}


@dataclass
class DomainInfo:
    host: str
    tier: str          # docs | repo | registry | qa | blog | unknown | blocked
    prior: float
    source_type: str   # maps onto document.source_type for trust scoring


def _suffixes(host: str) -> list[str]:
    """All dot-suffixes of a host, longest first: a.b.c -> [a.b.c, b.c, c]."""
    parts = host.split(".")
    return [".".join(parts[i:]) for i in range(len(parts))]


def classify(url: str) -> DomainInfo:
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    path = urlsplit(url).path.lower()
    for suffix in _suffixes(host):
        if suffix in _BLOCKED:
            return DomainInfo(host, "blocked", 0.0, "blog")
        if suffix in _HOSTS:
            tier, source_type = _HOSTS[suffix]
            # source forges: issues/PRs carry different trust than repo docs
            if tier == "repo":
                if "/issues/" in path:
                    source_type = "github_issue"
                elif "/pull/" in path or "/merge_requests/" in path:
                    source_type = "github_pr"
            return DomainInfo(host, tier, TIER_PRIORS[tier], source_type)
    return DomainInfo(host, "unknown", TIER_PRIORS["unknown"], "blog")


def rank_candidates(
    cands: list[Candidate], *, max_pages: int = 6
) -> list[tuple[Candidate, DomainInfo]]:
    """Order candidates by domain prior (desc), then provider rank; drop
    blocked hosts; cap at ``max_pages``."""
    scored = [(c, classify(c.url)) for c in cands]
    scored = [(c, d) for c, d in scored if d.tier != "blocked"]
    scored.sort(key=lambda cd: (-cd[1].prior, cd[0].rank))
    return scored[:max_pages]


def domain_confidence(cands: list[Candidate], *, top: int = 8) -> float:
    """Mean domain prior of the top discovery results — the "is this even a
    software question" signal. Uses raw provider order (pre-ranking) so one
    lucky dev-domain hit can't dominate."""
    if not cands:
        return 0.0
    sample = sorted(cands, key=lambda c: c.rank)[:top]
    return sum(classify(c.url).prior for c in sample) / len(sample)


def out_of_domain(cands: list[Candidate]) -> bool:
    return domain_confidence(cands) < OFF_DOMAIN_THRESHOLD

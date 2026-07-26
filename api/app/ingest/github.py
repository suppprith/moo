"""GitHub connector.

Ingests the "dark knowledge" of the seed repos — issues + comments, pull
requests, and releases — via the REST API. Captures ``author_association`` as a
trust-relevant role and reactions/comment counts as popularity signals.

Auth token is read from ``GITHUB_TOKEN`` / ``GH_TOKEN`` or ``gh auth token``.
Discussions (GraphQL only) are a documented follow-up.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from collections.abc import Iterator

from .base import Connector
from .fetcher import Fetcher
from .models import AUTHOR_ROLE, RawDoc
from .seeds import GITHUB_REPOS

log = logging.getLogger("moo.ingest.github")

API = "https://api.github.com"
_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


def _token() -> str | None:
    tok = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if tok:
        return tok.strip()
    try:
        out = subprocess.run(
            ["gh", "auth", "token"], capture_output=True, text=True, timeout=10
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


def _role(assoc: str | None) -> str:
    return AUTHOR_ROLE.get((assoc or "NONE").upper(), "none")


class GitHubConnector(Connector):
    name = "github"

    def __init__(
        self,
        conn,
        fetcher: Fetcher,
        repos: list[str] | None = None,
        *,
        max_issues: int = 30,
        max_comments: int = 50,
    ) -> None:
        super().__init__(conn, fetcher)
        self.repos = repos or GITHUB_REPOS
        self.max_issues = max_issues
        self.max_comments = max_comments

    @classmethod
    def default_fetcher(cls) -> Fetcher:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        tok = _token()
        if tok:
            headers["Authorization"] = f"Bearer {tok}"
        else:
            log.warning("no GitHub token found; running unauthenticated (60 req/hr)")
        return Fetcher(min_interval=0.2, obey_robots=False, headers=headers)

    def _paged(self, url: str, cap: int) -> Iterator[dict]:
        seen = 0
        while url and seen < cap:
            res = self.fetcher.get(url, obey_robots=False)
            if not res.ok:
                log.warning("github fetch failed %s: %s", url, res.error)
                return
            items = res.json
            if not isinstance(items, list):
                return
            for item in items:
                yield item
                seen += 1
                if seen >= cap:
                    return
            m = _NEXT_RE.search(res.header("link"))
            url = m.group(1) if m else ""

    def _issues(self, repo: str) -> Iterator[RawDoc]:
        url = f"{API}/repos/{repo}/issues?state=all&per_page=100"
        for issue in self._paged(url, self.max_issues):
            is_pr = "pull_request" in issue
            source_type = "github_pr" if is_pr else "github_issue"
            title = issue.get("title") or ""
            body = issue.get("body") or ""
            yield RawDoc(
                source_type=source_type,
                url=issue["html_url"],
                title=title,
                text=f"{title}\n\n{body}".strip(),
                author=(issue.get("user") or {}).get("login"),
                author_role=_role(issue.get("author_association")),
                published_at=issue.get("created_at"),
                updated_at=issue.get("updated_at"),
                popularity=(issue.get("reactions") or {}).get("total_count", 0),
                content_type="text/markdown",
                metadata={
                    "repo": repo,
                    "number": issue.get("number"),
                    "state": issue.get("state"),
                    "comment_count": issue.get("comments", 0),
                    "labels": [lbl.get("name") for lbl in issue.get("labels", [])],
                },
            )
            if issue.get("comments", 0):
                yield from self._comments(repo, issue)

    def _comments(self, repo: str, issue: dict) -> Iterator[RawDoc]:
        url = f"{issue['comments_url']}?per_page=100"
        for c in self._paged(url, self.max_comments):
            body = c.get("body") or ""
            if not body.strip():
                continue
            yield RawDoc(
                source_type="github_comment",
                url=c["html_url"],
                title=f"comment on #{issue.get('number')} {issue.get('title', '')}".strip(),
                text=body,
                author=(c.get("user") or {}).get("login"),
                author_role=_role(c.get("author_association")),
                published_at=c.get("created_at"),
                updated_at=c.get("updated_at"),
                popularity=(c.get("reactions") or {}).get("total_count", 0),
                content_type="text/markdown",
                metadata={"repo": repo, "parent": issue["html_url"]},
            )

    def _releases(self, repo: str) -> Iterator[RawDoc]:
        url = f"{API}/repos/{repo}/releases?per_page=100"
        for rel in self._paged(url, self.max_issues):
            body = rel.get("body") or ""
            name = rel.get("name") or rel.get("tag_name") or ""
            yield RawDoc(
                source_type="github_release",
                url=rel["html_url"],
                title=name,
                text=f"{name}\n\n{body}".strip(),
                author=(rel.get("author") or {}).get("login"),
                author_role="maintainer",
                published_at=rel.get("published_at"),
                content_type="text/markdown",
                metadata={"repo": repo, "tag": rel.get("tag_name")},
            )

    def fetch(self) -> Iterator[RawDoc]:
        for repo in self.repos:
            log.info("ingesting %s", repo)
            yield from self._issues(repo)
            yield from self._releases(repo)

"""Community connector: Stack Overflow, Hacker News, Reddit (SUP-75).

These carry the contrarian / experience-based evidence the evidence layer needs.
Each source is a method; ``fetch()`` runs the configured ones. Q&A threading is
preserved via ``metadata.parent`` (+ ``question_id``/``story_id``), scores are
stored as ``popularity`` (SO ``is_accepted`` is kept in metadata), and HN/Reddit
hits are filtered to the domain to keep noise out of the corpus.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from datetime import UTC, datetime

from .base import Connector
from .fetcher import Fetcher
from .models import RawDoc
from .seeds import HN_QUERIES, STACKOVERFLOW_TAGS, SUBREDDITS, is_domain_relevant

log = logging.getLogger("moo.ingest.community")

SE_API = "https://api.stackexchange.com/2.3"
HN_API = "https://hn.algolia.com/api/v1"


def _iso(epoch: int | float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


class CommunityConnector(Connector):
    name = "community"

    def __init__(
        self,
        conn,
        fetcher: Fetcher,
        sources: tuple[str, ...] = ("so", "hn", "reddit"),
        *,
        max_items: int = 15,
    ) -> None:
        super().__init__(conn, fetcher)
        self.sources = sources
        self.max_items = max_items  # per tag/query/subreddit

    @classmethod
    def default_fetcher(cls) -> Fetcher:
        # Documented APIs / .json endpoints: skip robots, stay polite.
        return Fetcher(min_interval=1.5, obey_robots=False)

    # -- Stack Overflow ------------------------------------------------------
    def _se_get(self, url: str) -> dict | None:
        """GET a Stack Exchange API URL, honoring the `backoff` field.

        SE returns {"backoff": N} with HTTP 200 when the client should pause N
        seconds before the next request to that method; ignoring it escalates
        to throttling and eventually an IP ban.
        """
        res = self.fetcher.get(url, obey_robots=False)
        if not res.ok or not isinstance(res.json, dict):
            log.warning("SE request failed %s: %s", url, res.error)
            return None
        payload = res.json
        backoff = payload.get("backoff")
        if backoff and not res.from_cache:
            log.info("SE backoff requested: sleeping %ss", backoff)
            time.sleep(float(backoff))
        return payload

    def _stackoverflow(self) -> Iterator[RawDoc]:
        for tag in STACKOVERFLOW_TAGS:
            url = (
                f"{SE_API}/questions?order=desc&sort=votes&tagged={tag}"
                f"&site=stackoverflow&filter=withbody&pagesize={self.max_items}"
            )
            payload = self._se_get(url)
            if payload is None:
                continue
            for q in payload.get("items", []):
                qurl = q.get("link")
                yield RawDoc(
                    source_type="so_question",
                    url=qurl,
                    title=q.get("title"),
                    text=q.get("body") or q.get("title") or "",
                    author=(q.get("owner") or {}).get("display_name"),
                    published_at=_iso(q.get("creation_date")),
                    popularity=q.get("score"),
                    content_type="text/html",
                    metadata={"tag": tag, "tags": q.get("tags"), "qid": q.get("question_id")},
                )
                yield from self._so_answers(q, tag)

    def _so_answers(self, question: dict, tag: str) -> Iterator[RawDoc]:
        qid = question.get("question_id")
        url = (
            f"{SE_API}/questions/{qid}/answers?order=desc&sort=votes"
            f"&site=stackoverflow&filter=withbody&pagesize={self.max_items}"
        )
        payload = self._se_get(url)
        if payload is None:
            return
        for a in payload.get("items", []):
            yield RawDoc(
                source_type="so_answer",
                url=a.get("link") or f"{question.get('link')}#{a.get('answer_id')}",
                title=f"Answer to: {question.get('title', '')}".strip(),
                text=a.get("body") or "",
                author=(a.get("owner") or {}).get("display_name"),
                published_at=_iso(a.get("creation_date")),
                popularity=a.get("score"),
                content_type="text/html",
                metadata={
                    "parent": question.get("link"),
                    "question_id": qid,
                    "accepted": bool(a.get("is_accepted")),
                    "tag": tag,
                },
            )

    # -- Hacker News (Algolia) ----------------------------------------------
    def _hackernews(self) -> Iterator[RawDoc]:
        for query in HN_QUERIES:
            q = query.replace(" ", "+")
            url = f"{HN_API}/search?query={q}&tags=story&hitsPerPage={self.max_items}"
            res = self.fetcher.get(url, obey_robots=False)
            if not res.ok or not isinstance(res.json, dict):
                log.warning("HN search failed for %r: %s", query, res.error)
                continue
            for hit in res.json.get("hits", []):
                title = hit.get("title") or ""
                body = hit.get("story_text") or ""
                if not is_domain_relevant(title, body):
                    continue
                oid = hit.get("objectID")
                yield RawDoc(
                    source_type="hn_story",
                    url=f"https://news.ycombinator.com/item?id={oid}",
                    title=title,
                    text=f"{title}\n\n{body}".strip(),
                    author=hit.get("author"),
                    published_at=hit.get("created_at"),
                    popularity=hit.get("points"),
                    content_type="text/html",
                    metadata={
                        "query": query,
                        "external_url": hit.get("url"),
                        "num_comments": hit.get("num_comments"),
                    },
                )

    # -- Reddit --------------------------------------------------------------
    def _reddit(self) -> Iterator[RawDoc]:
        for sub in SUBREDDITS:
            url = f"https://www.reddit.com/r/{sub}/top.json?t=year&limit={self.max_items}"
            res = self.fetcher.get(url, obey_robots=False)
            if not res.ok or not isinstance(res.json, dict):
                log.warning("Reddit fetch failed for r/%s: %s (may require OAuth)", sub, res.error)
                continue
            for child in res.json.get("data", {}).get("children", []):
                p = child.get("data", {})
                title, body = p.get("title") or "", p.get("selftext") or ""
                if not is_domain_relevant(title, body):
                    continue
                yield RawDoc(
                    source_type="reddit_post",
                    url=f"https://www.reddit.com{p.get('permalink', '')}",
                    title=title,
                    text=f"{title}\n\n{body}".strip(),
                    author=p.get("author"),
                    published_at=_iso(p.get("created_utc")),
                    popularity=p.get("score"),
                    content_type="text/plain",
                    metadata={"subreddit": sub, "num_comments": p.get("num_comments")},
                )

    # -- driver --------------------------------------------------------------
    def fetch(self) -> Iterator[RawDoc]:
        dispatch = {"so": self._stackoverflow, "hn": self._hackernews, "reddit": self._reddit}
        for src in self.sources:
            fn = dispatch.get(src)
            if fn is None:
                continue
            log.info("community source: %s", src)
            yield from fn()

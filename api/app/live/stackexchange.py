"""Stack Exchange pages through the Stack Exchange API instead of HTML.

Stack Overflow's robots.txt disallows every path and the site answers a plain
crawler with 403, so fetching a question page as HTML fails for every
candidate. It is also the host discovery returns most often for coding queries,
so a live search that scrapes it spends most of its page budget on refusals.

The documented API serves the same question and answers as JSON. This module
turns a discovered question URL into one document: the question, then its
top-voted answers in vote order, each headed with its score, whether it was
accepted, and when it was written. The dates matter: a stale answer is usually
the old, accepted, high-scoring one, and the evidence layer needs to see that.

All question URLs from one site are fetched in two API calls (questions, then
answers), so a query costs at most two requests per Stack Exchange site however
many of its questions discovery returned. Without a key the API allows 300
requests a day per IP; ``MOO_STACKEXCHANGE_KEY`` raises that to 10,000.
"""

from __future__ import annotations

import html
import logging
import os
import re
import time
from datetime import UTC, datetime
from urllib.parse import urlencode, urlsplit

from ..ingest.models import RawDoc

log = logging.getLogger("moo.live.stackexchange")

SE_API = "https://api.stackexchange.com/2.3"
ANSWERS_PER_QUESTION = 4

_SITES = {
    "stackoverflow.com": "stackoverflow",
    "serverfault.com": "serverfault",
    "superuser.com": "superuser",
    "askubuntu.com": "askubuntu",
    "mathoverflow.net": "mathoverflow",
}
_QUESTION_PATH = re.compile(r"^/(?:questions|q)/(\d+)")

# The API sends {"backoff": N} when a client must pause before calling again.
# Live search can't sleep for it, so it skips the API until the pause is over.
_backoff_until = 0.0


def question_ref(url: str) -> tuple[str, int] | None:
    """``(api_site, question_id)`` for a Stack Exchange question URL, else None.
    Answer permalinks (``/a/123``) are not question ids and are left alone."""
    parts = urlsplit(url)
    host = parts.netloc.lower().removeprefix("www.")
    site = _SITES.get(host)
    if site is None and host.endswith(".stackexchange.com"):
        site = host.removesuffix(".stackexchange.com")
        if site.startswith("meta.") or site == "meta":
            return None
    if site is None:
        return None
    m = _QUESTION_PATH.match(parts.path)
    return (site, int(m.group(1))) if m else None


def _iso(epoch: int | float | None) -> str | None:
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=UTC).isoformat()


def body_to_markdown(body: str | None) -> str:
    """The API's HTML body as plain markdown, keeping code verbatim. Code is
    where the old and new API names live, so it must survive intact."""
    if not body:
        return ""
    text = re.sub(
        r"<pre[^>]*>\s*<code[^>]*>(.*?)</code>\s*</pre>",
        lambda m: "\n```\n" + m.group(1).strip("\n") + "\n```\n",
        body, flags=re.S | re.I,
    )
    text = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", text, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</(p|li|h\d|blockquote|div)>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _params(site: str, **extra) -> str:
    params = {"site": site, "filter": "withbody", **extra}
    key = os.environ.get("MOO_STACKEXCHANGE_KEY")
    if key:
        params["key"] = key
    return urlencode(params)


def _get(fetcher, url: str) -> dict | None:
    global _backoff_until
    res = fetcher.get(url, obey_robots=False)
    if not res.ok:
        log.warning("stack exchange API failed: %s", res.error)
        return None
    try:
        payload = res.json
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("backoff") and not res.from_cache:
        _backoff_until = time.monotonic() + float(payload["backoff"])
    if payload.get("error_id"):
        log.warning("stack exchange API error %s: %s",
                    payload.get("error_id"), payload.get("error_message"))
        return None
    return payload


def fetch_questions(fetcher, site: str, qids: list[int], *,
                    urls: dict[int, str] | None = None,
                    answers: int = ANSWERS_PER_QUESTION) -> dict[int, RawDoc]:
    """Fetch questions ``qids`` from ``site`` with their top answers, one
    document per question. ``urls`` maps an id to the URL discovery returned,
    which becomes the document URL so the cache is keyed on what the pipeline
    looks up. Never raises; a failed call returns what it has, maybe nothing."""
    if not qids or time.monotonic() < _backoff_until:
        return {}
    ids = ";".join(str(q) for q in qids[:100])
    qpayload = _get(fetcher, f"{SE_API}/questions/{ids}?{_params(site)}")
    if not qpayload:
        return {}
    apayload = _get(fetcher, f"{SE_API}/questions/{ids}/answers?"
                             f"{_params(site, sort='votes', order='desc', pagesize=100)}") or {}

    by_question: dict[int, list[dict]] = {}
    for a in apayload.get("items") or []:
        by_question.setdefault(a.get("question_id"), []).append(a)

    docs: dict[int, RawDoc] = {}
    for q in qpayload.get("items") or []:
        qid = q.get("question_id")
        title = html.unescape(q.get("title") or "")
        parts = [f"# {title}", body_to_markdown(q.get("body"))]
        top = sorted(by_question.get(qid, []), key=lambda a: -(a.get("score") or 0))[:answers]
        for a in top:
            when = (_iso(a.get("last_edit_date") or a.get("creation_date")) or "")[:10]
            label = f"## Answer (score {a.get('score', 0)}"
            if a.get("is_accepted"):
                label += ", accepted"
            label += f", {when})" if when else ")"
            parts += [label, body_to_markdown(a.get("body"))]
        url = (urls or {}).get(qid) or q.get("link")
        docs[qid] = RawDoc(
            # Most of the text is answers, so it trusts and ranks as one.
            source_type="so_answer",
            url=url,
            title=title or url,
            text="\n\n".join(p for p in parts if p),
            author=(q.get("owner") or {}).get("display_name"),
            published_at=_iso(q.get("creation_date")),
            updated_at=_iso(q.get("last_activity_date")),
            popularity=q.get("score"),
            content_type="text/markdown",
            metadata={
                "qid": qid,
                "tags": q.get("tags"),
                "answers": len(top),
                "accepted_answer_id": q.get("accepted_answer_id"),
                "via": "stackexchange_api",
            },
        )
    return docs

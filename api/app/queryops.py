"""Typed query operators (SUP-103).

Power-user syntax parsed out of the query string *before* retrieval, so the
same syntax works everywhere a query enters moo (web UI, CLI, MCP tools, the
web_search adapter):

    type:docs,so       filter by source type (aliases: issue, pr, release,
                       docs, blog, so/qa, hn, reddit)
    site:github.com    only results from a host (suffix match)
    since:2024[-05]    only content published/updated after a date
    lang:python        soft hint — the language is folded into the query text
    "exact phrase"     result text must contain the phrase
    -word              exclude results whose text contains the word

Everything else is retrieval text. **Invalid operators fail soft**: an
unrecognized value (``type:banana``, ``since:soon``) is kept as plain query
text and reported in ``invalid`` so a UI can hint at it — a typo never turns
into an empty result page.

``type:``/``since:`` map onto the index filters ``retrieve()`` already has;
``site:``/phrases/exclusions are post-filters over the hit text and URL
(applied in ``app.search`` with over-fetch so a filtered page stays full).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# operator value -> document.source_type list
TYPE_ALIASES: dict[str, list[str]] = {
    "docs": ["docs"],
    "issue": ["github_issue"],
    "issues": ["github_issue"],
    "pr": ["github_pr"],
    "prs": ["github_pr"],
    "release": ["github_release"],
    "releases": ["github_release"],
    "blog": ["blog"],
    "so": ["so"],
    "stackoverflow": ["so"],
    "qa": ["so"],
    "hn": ["hn"],
    "reddit": ["reddit"],
    "github": ["github_issue", "github_pr", "github_release", "github_comment"],
}

_OPERATOR = re.compile(r'^(?P<name>[a-zA-Z]+):(?P<value>\S+)$')
_YEAR = re.compile(r"^\d{4}$")
_YEAR_MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_FULL_DATE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")
# quoted phrases first so operators/exclusions inside quotes stay literal text
_TOKEN = re.compile(r'"([^"]+)"|(\S+)')


@dataclass
class ParsedQuery:
    text: str                                  # residual retrieval text
    source_types: list[str] | None = None      # type:  -> retrieve(source_types=)
    since: str | None = None                   # since: -> retrieve(since=)
    site: str | None = None                    # site:  -> post-filter on host
    phrases: list[str] = field(default_factory=list)   # "..." -> must appear
    excludes: list[str] = field(default_factory=list)  # -word -> must not appear
    invalid: list[str] = field(default_factory=list)   # kept as text, hint in UI

    @property
    def active(self) -> bool:
        """True when any operator changed retrieval (drives over-fetch + meta)."""
        return bool(self.source_types or self.since or self.site
                    or self.phrases or self.excludes)

    @property
    def has_post_filters(self) -> bool:
        return bool(self.site or self.phrases or self.excludes)

    def describe(self) -> dict:
        """Compact summary for `meta.operators` (only what was recognized)."""
        out: dict = {}
        for key in ("source_types", "since", "site", "phrases", "excludes", "invalid"):
            value = getattr(self, key)
            if value:
                out[key] = value
        return out


def _since_date(value: str) -> str | None:
    if _YEAR.match(value):
        return f"{value}-01-01"
    if _YEAR_MONTH.match(value):
        return f"{value}-01"
    if _FULL_DATE.match(value):
        return value
    return None


def parse(query: str) -> ParsedQuery:
    """Split ``query`` into operators + residual retrieval text (fail-soft)."""
    text_parts: list[str] = []
    parsed = ParsedQuery(text="")

    for m in _TOKEN.finditer(query):
        phrase, token = m.group(1), m.group(2)
        if phrase is not None:
            parsed.phrases.append(phrase)
            text_parts.append(phrase)  # the phrase still guides retrieval
            continue
        # exclusion must look like -word: "-9" (signal) or "--flag" (CLI option)
        # are query text, not exclusions
        if token.startswith("-") and len(token) > 1 and token[1].isalpha():
            parsed.excludes.append(token[1:].lower())
            continue
        op = _OPERATOR.match(token)
        if op is None:
            text_parts.append(token)
            continue
        name, value = op.group("name").lower(), op.group("value")
        if name == "type":
            types: list[str] = []
            unknown = [v for v in value.lower().split(",") if v not in TYPE_ALIASES]
            if unknown:
                parsed.invalid.append(token)
                text_parts.append(token)
                continue
            for v in value.lower().split(","):
                types.extend(t for t in TYPE_ALIASES[v] if t not in types)
            parsed.source_types = (parsed.source_types or []) + [
                t for t in types if t not in (parsed.source_types or [])
            ]
        elif name == "site":
            parsed.site = value.lower().removeprefix("www.")
        elif name == "since":
            date = _since_date(value)
            if date is None:
                parsed.invalid.append(token)
                text_parts.append(token)
            else:
                parsed.since = date
        elif name == "lang":
            # soft hint: fold the language into the retrieval text (document
            # lang metadata is too sparse to hard-filter on without emptying
            # results — fail-soft principle)
            text_parts.append(value)
        else:
            # unknown operator: plain text (e.g. a URL fragment or "re:invent")
            parsed.invalid.append(token)
            text_parts.append(token)

    parsed.text = " ".join(text_parts).strip()
    return parsed


def _host(url: str | None) -> str:
    from urllib.parse import urlsplit

    return (urlsplit(url or "").netloc or "").lower().removeprefix("www.")


def matches(parsed: ParsedQuery, *, text: str | None, url: str | None) -> bool:
    """Post-filter one hit against site/phrase/exclusion operators."""
    if parsed.site:
        host = _host(url)
        if not (host == parsed.site or host.endswith("." + parsed.site)):
            return False
    lowered = (text or "").lower()
    for phrase in parsed.phrases:
        if phrase.lower() not in lowered:
            return False
    for word in parsed.excludes:
        if word in lowered:
            return False
    return True

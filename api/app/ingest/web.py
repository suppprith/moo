"""Docs & engineering-blog connector (SUP-74).

Discovers pages (explicit seeds or a sitemap), fetches them through the shared
Fetcher (robots-respecting), and extracts clean main content as **markdown**
with trafilatura — which preserves heading structure and fenced code blocks
verbatim. Highlight-class languages (``language-sql`` etc.) are collected into
``metadata.code_langs`` since trafilatura's markdown drops the inline tag.
Publish/update dates come from page metadata where available.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterator

import trafilatura

from .base import Connector
from .fetcher import Fetcher
from .models import RawDoc
from .seeds import DOCS_SITES, DocsSite

log = logging.getLogger("moo.ingest.web")

_LANG_RE = re.compile(r'class="[^"]*(?:language|lang|highlight-source)-([a-zA-Z0-9+#]+)', re.I)


def _code_langs(html: str) -> list[str]:
    return sorted({m.group(1).lower() for m in _LANG_RE.finditer(html)})


class DocsConnector(Connector):
    name = "docs"

    def __init__(
        self,
        conn,
        fetcher: Fetcher,
        sites: list[DocsSite] | None = None,
        *,
        max_pages: int = 20,
    ) -> None:
        super().__init__(conn, fetcher)
        self.sites = sites or DOCS_SITES
        self.max_pages = max_pages

    @classmethod
    def default_fetcher(cls) -> Fetcher:
        # Real crawling: respect robots, be polite.
        return Fetcher(min_interval=1.0, obey_robots=True)

    # -- url discovery -------------------------------------------------------
    def _sitemap_urls(self, sitemap_url: str, site: DocsSite) -> list[str]:
        res = self.fetcher.get(sitemap_url)
        if not res.ok:
            log.warning("sitemap fetch failed %s: %s", sitemap_url, res.error)
            return []
        try:
            root = ET.fromstring(res.text)
        except ET.ParseError as exc:
            log.warning("sitemap parse failed %s: %s", sitemap_url, exc)
            return []
        locs = [el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text]
        # nested sitemap index -> pull a few child sitemaps
        if root.tag.endswith("sitemapindex"):
            urls: list[str] = []
            for child in locs[:3]:
                urls.extend(self._sitemap_urls(child, site))
                if len(urls) >= self.max_pages:
                    break
            locs = urls
        if site.allow_prefix:
            locs = [u for u in locs if u.startswith(site.allow_prefix)]
        return locs[: self.max_pages]

    def _urls_for(self, site: DocsSite) -> list[str]:
        if site.pages:
            return site.pages[: self.max_pages]
        if site.sitemap:
            return self._sitemap_urls(site.sitemap, site)
        return []

    # -- extraction ----------------------------------------------------------
    def _extract(self, site: DocsSite, url: str, html: str) -> RawDoc | None:
        md = trafilatura.extract(
            html, output_format="markdown", include_formatting=True, favor_recall=True
        )
        if not md or not md.strip():
            log.info("no extractable content at %s", url)
            return None

        title = url
        published = author = None
        try:
            meta = trafilatura.bare_extraction(html, url=url, with_metadata=True)
            if meta is not None:
                title = getattr(meta, "title", None) or url
                published = getattr(meta, "date", None)
                author = getattr(meta, "author", None)
        except Exception as exc:  # noqa: BLE001 - metadata is best-effort
            log.debug("metadata extraction failed for %s: %s", url, exc)

        langs = _code_langs(html)
        return RawDoc(
            source_type=site.source_type,
            url=url,
            title=title,
            text=md,
            author=author,
            published_at=published,
            content_type="text/markdown",
            metadata={"site": site.name, "code_langs": langs},
        )

    def fetch(self) -> Iterator[RawDoc]:
        for site in self.sites:
            log.info("crawling %s", site.name)
            for url in self._urls_for(site):
                res = self.fetcher.get(url)
                if not res.ok:
                    log.warning("skip %s: %s", url, res.error)
                    continue
                doc = self._extract(site, url, res.text)
                if doc is not None:
                    yield doc

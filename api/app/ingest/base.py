"""Connector interface + `document` upsert.

A connector yields ``RawDoc``s from ``fetch()``; ``run()`` stores each one,
catching per-item errors so one failure never aborts the crawl. Upsert is keyed
on ``document.url`` and skips rows whose content hash is unchanged, which is what
makes an incremental re-crawl cheap.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field

from .fetcher import Fetcher
from .models import RawDoc

log = logging.getLogger("moo.ingest")


@dataclass
class IngestStats:
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0
    error_urls: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return self.inserted + self.updated + self.unchanged + self.errors

    def __str__(self) -> str:
        return (
            f"{self.total} docs: {self.inserted} new, {self.updated} updated, "
            f"{self.unchanged} unchanged, {self.errors} errors"
        )


def store(conn: sqlite3.Connection, doc: RawDoc) -> str:
    """Upsert one doc. Returns 'inserted' | 'updated' | 'unchanged'.

    ``content_hash`` excludes volatile signals (votes, reactions) so they don't
    churn the store, but they feed trust scoring, so refresh them regardless.
    """
    new_hash = doc.content_hash()
    row = conn.execute("SELECT id, content_hash FROM document WHERE url = ?", (doc.url,)).fetchone()
    if row is not None and row["content_hash"] == new_hash:
        conn.execute(
            "UPDATE document SET popularity = coalesce(?, popularity), "
            "updated_at = coalesce(?, updated_at), fetched_at = datetime('now') "
            "WHERE id = ?",
            (doc.popularity, doc.updated_at, row["id"]),
        )
        conn.commit()
        return "unchanged"

    params = (
        doc.source_type, doc.url, doc.title, doc.author, doc.author_role,
        doc.published_at, doc.updated_at, new_hash, doc.popularity,
        doc.content_type, doc.lang, doc.text,
        json.dumps(doc.metadata, ensure_ascii=False) if doc.metadata else None,
    )
    conn.execute(
        """
        INSERT INTO document (
            source_type, url, title, author, author_role, published_at, updated_at,
            content_hash, popularity, content_type, lang, raw_text, metadata, fetched_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
        ON CONFLICT(url) DO UPDATE SET
            source_type = excluded.source_type,
            title       = excluded.title,
            author      = excluded.author,
            author_role = excluded.author_role,
            published_at= excluded.published_at,
            updated_at  = excluded.updated_at,
            content_hash= excluded.content_hash,
            popularity  = excluded.popularity,
            content_type= excluded.content_type,
            lang        = excluded.lang,
            raw_text    = excluded.raw_text,
            metadata    = excluded.metadata,
            fetched_at  = datetime('now')
        """,
        params,
    )
    conn.commit()
    return "updated" if row is not None else "inserted"


class Connector(ABC):
    """Base class for all source connectors."""

    name: str = "connector"

    def __init__(self, conn: sqlite3.Connection, fetcher: Fetcher) -> None:
        self.conn = conn
        self.fetcher = fetcher

    @classmethod
    def default_fetcher(cls) -> Fetcher:
        """Fetcher configured for this source; override for auth/robots/rate."""
        return Fetcher()

    @abstractmethod
    def fetch(self) -> Iterator[RawDoc]:
        """Yield raw documents. Implementations should be resumable/idempotent."""

    def run(self, limit: int | None = None) -> IngestStats:
        stats = IngestStats()
        for i, doc in enumerate(self.fetch()):
            if limit is not None and i >= limit:
                break
            try:
                result = store(self.conn, doc)
                setattr(stats, result, getattr(stats, result) + 1)
            except Exception as exc:  # noqa: BLE001
                stats.errors += 1
                stats.error_urls.append(doc.url)
                log.warning("store failed for %s: %s", doc.url, exc)
        return stats

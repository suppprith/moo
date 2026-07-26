"""Shared ingestion data types."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

AUTHOR_ROLE = {
    "OWNER": "maintainer",
    "MEMBER": "maintainer",
    "COLLABORATOR": "maintainer",
    "CONTRIBUTOR": "contributor",
    "FIRST_TIME_CONTRIBUTOR": "contributor",
    "FIRST_TIMER": "none",
    "NONE": "none",
}


@dataclass
class RawDoc:
    """One ingested source, ready to upsert into the `document` table."""

    source_type: str

    url: str
    text: str = ""
    title: str | None = None
    author: str | None = None
    author_role: str | None = None
    published_at: str | None = None
    updated_at: str | None = None
    popularity: int | None = None
    content_type: str | None = None
    lang: str | None = None
    metadata: dict = field(default_factory=dict)

    def content_hash(self) -> str:
        """Stable hash of the fields that mean 'the content changed'."""
        payload = json.dumps(
            [self.title, self.text, self.author, self.published_at],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

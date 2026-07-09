"""Shared ingestion data types."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

# GitHub `author_association` -> our trust-relevant role (Phase 4 input).
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

    source_type: str  # github_issue | github_pr | docs | blog | so | hn | reddit

    url: str
    text: str = ""
    title: str | None = None
    author: str | None = None
    author_role: str | None = None
    published_at: str | None = None        # ISO-8601
    updated_at: str | None = None          # ISO-8601, source-side last modification
    popularity: int | None = None          # reactions / score / upvotes
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

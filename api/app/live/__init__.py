"""Live retrieval: per-query discovery + fetch of web pages (SUP-130).

moo's pivot from "search a pre-built corpus" to "search the live web":

- ``providers``  — pluggable URL discovery via an existing search index
                   (SearXNG self-hosted/keyless, Brave Search API/key).
                   moo does NOT crawl the open web; it discovers candidates
                   through a search provider, then fetches + reasons itself.
- ``policy``     — the software-domain source policy: trusted dev domains get
                   a prior boost, junk is dropped, and clearly non-software
                   queries are flagged out-of-domain instead of answered badly.
- ``pipeline``   — the per-query orchestrator: discover -> rank by policy ->
                   fetch + extract -> write through the standard ingest path
                   (document -> chunk -> embed -> index). Because live pages
                   land in the same store, ``retrieve()`` and the evidence
                   layer work over "cache + just-fetched" unchanged, and the
                   store *is* the cache (SUP-144).

CLI:  ``uv run python -m app.live "how does postgres vacuum work"``
"""

from .pipeline import live_fetch  # noqa: F401
from .providers import resolve_provider  # noqa: F401

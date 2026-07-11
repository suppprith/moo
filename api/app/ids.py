"""Stable, prefixed handles for the agent-facing API (SUP-105).

Every source/claim/chunk/entity in an agent-shaped response carries an opaque
string handle like ``chk_42`` instead of a bare integer rowid. Callers treat the
handle as opaque; the fetch/drill-down endpoints (SUP-106) decode the prefix to
route the lookup.

The handles are stable (backed by SQLite rowids) and deliberately
human-debuggable — a prefixed int reads better in logs than base64, and the
prefix stops an agent from passing a chunk handle where a claim handle is
expected (``decode`` returns the kind so the endpoint can reject a mismatch).
"""

from __future__ import annotations

# kind -> prefix. A search result "source" is fundamentally a chunk-in-a-document,
# so it is addressed with a CHUNK handle; DOCUMENT/ENTITY are for other surfaces.
DOCUMENT = "doc"
CHUNK = "chk"
CLAIM = "clm"
ENTITY = "ent"

_KINDS = frozenset({DOCUMENT, CHUNK, CLAIM, ENTITY})


def encode(kind: str, rowid: int) -> str:
    """``encode("chk", 42) -> "chk_42"``."""
    if kind not in _KINDS:
        raise ValueError(f"unknown handle kind: {kind!r}")
    return f"{kind}_{int(rowid)}"


def decode(handle: str) -> tuple[str, int]:
    """Split a handle into ``(kind, rowid)``. Raises ``ValueError`` on a malformed
    or unknown-prefix handle so callers can return a clean 4xx."""
    prefix, sep, rest = handle.partition("_")
    if not sep or prefix not in _KINDS:
        raise ValueError(f"malformed handle: {handle!r}")
    try:
        return prefix, int(rest)
    except ValueError:
        raise ValueError(f"malformed handle: {handle!r}") from None

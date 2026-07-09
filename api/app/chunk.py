"""Cleaning & chunking pipeline (SUP-76).

Turns ``document.raw_text`` into ``chunk`` rows:

- **clean**: HTML bodies (Stack Overflow / HN answers) are converted to markdown
  with code blocks preserved; markdown/plain sources pass through.
- **chunk**: heading-aware, ~300-500 token windows with overlap. Fenced code
  blocks are atomic — a chunk boundary never lands inside one.
- **dedup**: exact-duplicate chunk text is collapsed via ``canonical_chunk_id``
  (the first occurrence stays canonical; later copies point at it).

Per-chunk metadata: ``heading`` + ``url_anchor`` live on the chunk; source_type,
date and author_role come from the parent ``document`` via ``document_id``.

Run:  ``uv run python -m app.chunk``
"""

from __future__ import annotations

import argparse
import hashlib
import html as html_lib
import logging
import re
import sqlite3
from collections.abc import Iterator

from .db import get_connection, migrate

log = logging.getLogger("moo.chunk")

TARGET_TOKENS = 450
MIN_TOKENS = 120  # don't flush at a heading until at least this big (avoids tiny chunks)
OVERLAP_TOKENS = 60

_CODE_LANG_RE = re.compile(r'class="[^"]*(?:language|lang|highlight-source)-([a-z0-9+#]+)', re.I)
_FENCE_RE = re.compile(r"```.*?```", re.S)
_HEADING_RE = re.compile(r"^#{1,6}\s")


# ---------------------------------------------------------------------------
# cleaning
# ---------------------------------------------------------------------------

def _html_to_markdown(text: str) -> str:
    """Best-effort HTML fragment -> markdown, preserving code verbatim."""
    codes: list[str] = []

    def _stash_pre(m: re.Match[str]) -> str:
        inner = m.group(0)
        lang_m = _CODE_LANG_RE.search(inner)
        lang = lang_m.group(1) if lang_m else ""
        code = html_lib.unescape(re.sub(r"<[^>]+>", "", inner))
        codes.append(f"```{lang}\n{code.strip()}\n```")
        return f"\n\n\x00CODE{len(codes) - 1}\x00\n\n"

    s = re.sub(r"<pre\b.*?</pre>", _stash_pre, text, flags=re.S | re.I)
    s = re.sub(r"<h([1-6])[^>]*>", lambda m: "\n\n" + "#" * int(m.group(1)) + " ", s, flags=re.I)
    s = re.sub(
        r"<code\b[^>]*>(.*?)</code>",
        lambda m: "`" + re.sub(r"<[^>]+>", "", m.group(1)) + "`",
        s,
        flags=re.S | re.I,
    )
    s = re.sub(r"<li\b[^>]*>", "\n- ", s, flags=re.I)
    s = re.sub(r"</(p|div|h[1-6]|ul|ol|li|blockquote|tr|table)>", "\n\n", s, flags=re.I)
    s = re.sub(r"<br\s*/?>", "\n", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)  # strip anything left
    s = html_lib.unescape(s)
    for i, block in enumerate(codes):
        s = s.replace(f"\x00CODE{i}\x00", block)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def clean(raw_text: str, content_type: str | None) -> str:
    if content_type and "html" in content_type:
        return _html_to_markdown(raw_text)
    return raw_text.strip()


# ---------------------------------------------------------------------------
# chunking
# ---------------------------------------------------------------------------

def est_tokens(text: str) -> int:
    return max(1, round(len(text.split()) * 1.3))


def _slug(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-")


def _split_sections(md: str) -> list[tuple[str | None, str]]:
    """Split markdown into (heading, section_text) by markdown headings."""
    sections: list[tuple[str | None, str]] = []
    head: str | None = None
    buf: list[str] = []
    for line in md.splitlines():
        if _HEADING_RE.match(line):
            if buf:
                sections.append((head, "\n".join(buf).strip()))
            head = line.lstrip("#").strip()
            buf = [line]
        else:
            buf.append(line)
    if buf:
        sections.append((head, "\n".join(buf).strip()))
    return [(h, t) for h, t in sections if t]


def _blocks(section: str) -> list[tuple[str, str]]:
    """Break a section into ('code'|'text', body) blocks; code stays whole."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _FENCE_RE.finditer(section):
        for para in re.split(r"\n\s*\n", section[pos : m.start()]):
            if para.strip():
                out.append(("text", para.strip()))
        out.append(("code", m.group(0)))
        pos = m.end()
    for para in re.split(r"\n\s*\n", section[pos:]):
        if para.strip():
            out.append(("text", para.strip()))
    return out


def _overlap_tail(blocks: list[tuple[str, str]]) -> tuple[list[tuple[str, str]], int]:
    """Trailing text blocks (no code) summing up to the overlap budget."""
    tail: list[tuple[str, str]] = []
    tokens = 0
    for kind, body in reversed(blocks):
        if kind == "code":
            break
        t = est_tokens(body)
        if tokens + t > OVERLAP_TOKENS:
            break
        tail.insert(0, (kind, body))
        tokens += t
    return tail, tokens


def _doc_blocks(md: str) -> Iterator[tuple[str | None, str, str]]:
    """Flatten a document into (heading, kind, body) blocks across all sections."""
    for heading, section in _split_sections(md):
        for kind, body in _blocks(section):
            yield heading, kind, body


def chunk_markdown(md: str) -> Iterator[tuple[str | None, str]]:
    """Yield (heading, chunk_text). Headings are soft boundaries (a new chunk is
    preferred at a heading but only once past MIN_TOKENS, so heading-dense pages
    don't produce tiny chunks). A fenced code block is never split."""
    cur: list[tuple[str, str]] = []
    cur_tokens = 0
    start_heading: str | None = None

    def join(blocks: list[tuple[str, str]]) -> str:
        return "\n\n".join(b for _, b in blocks)

    for heading, kind, body in _doc_blocks(md):
        t = est_tokens(body)
        # prefer a boundary at a heading, once the current chunk is big enough
        if cur and heading != start_heading and cur_tokens >= MIN_TOKENS:
            yield start_heading, join(cur)
            cur, cur_tokens = _overlap_tail(cur)
            start_heading = heading
        if not cur:
            start_heading = heading
        # a code block bigger than the budget becomes its own chunk
        if kind == "code" and t > TARGET_TOKENS:
            if cur:
                yield start_heading, join(cur)
                cur, cur_tokens = [], 0
            yield heading, body
            start_heading = None
            continue
        if cur and cur_tokens + t > TARGET_TOKENS:
            yield start_heading, join(cur)
            cur, cur_tokens = _overlap_tail(cur)
            start_heading = start_heading if cur else heading
        cur.append((kind, body))
        cur_tokens += t
    if cur:
        yield start_heading, join(cur)


# ---------------------------------------------------------------------------
# pipeline
# ---------------------------------------------------------------------------

def rechunk(conn: sqlite3.Connection, *, only_new: bool = False) -> dict[str, int]:
    seen: dict[str, int] = {}
    stats = {"documents": 0, "chunks": 0, "duplicates": 0}
    rows = conn.execute(
        "SELECT id, url, content_type FROM document "
        "WHERE raw_text IS NOT NULL AND length(raw_text) > 0"
    ).fetchall()
    if not only_new:
        # full rebuild: clear in one shot (per-doc deletes would trip the
        # canonical_chunk_id self-FK when a dup in another doc points at the row)
        conn.execute("DELETE FROM chunk")
    for doc in rows:
        if only_new and conn.execute(
            "SELECT 1 FROM chunk WHERE document_id = ? LIMIT 1", (doc["id"],)
        ).fetchone():
            continue
        raw = conn.execute("SELECT raw_text FROM document WHERE id = ?", (doc["id"],)).fetchone()[0]
        md = clean(raw, doc["content_type"])
        stats["documents"] += 1
        for ordinal, (heading, text) in enumerate(chunk_markdown(md)):
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            canonical = seen.get(digest)
            anchor = doc["url"] + (f"#{_slug(heading)}" if heading else "")
            cur = conn.execute(
                """
                INSERT INTO chunk (document_id, ordinal, heading, text, token_count,
                                   url_anchor, content_hash, canonical_chunk_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (doc["id"], ordinal, heading, text, est_tokens(text), anchor, digest, canonical),
            )
            if canonical is None:
                seen[digest] = cur.lastrowid
            else:
                stats["duplicates"] += 1
            stats["chunks"] += 1
    conn.commit()
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.chunk", description="Clean + chunk documents")
    parser.add_argument("--only-new", action="store_true", help="skip docs already chunked")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )
    migrate()
    conn = get_connection()
    stats = rechunk(conn, only_new=args.only_new)
    print(
        f"chunked {stats['documents']} docs -> {stats['chunks']} chunks "
        f"({stats['duplicates']} exact-dup collapsed)"
    )
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

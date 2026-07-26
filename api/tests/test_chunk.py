"""Unit tests for the cleaning & chunking pipeline (app.chunk)."""

from app.chunk import (
    MIN_TOKENS,
    TARGET_TOKENS,
    _html_to_markdown,
    chunk_markdown,
    est_tokens,
)

LOREM = (
    "The quick brown fox jumps over the lazy dog and keeps running through "
    "the field toward the river where the water flows quietly past the stones. "
)


def para(n_repeats: int) -> str:
    return (LOREM * n_repeats).strip()


def test_code_fence_never_split():
    code = "```sql\n" + "SELECT * FROM t WHERE id = 1;\n" * 120 + "```"
    md = f"# Heading\n\n{para(3)}\n\n{code}\n\n{para(3)}"
    chunks = list(chunk_markdown(md))
    for _, text in chunks:
        assert text.count("```") % 2 == 0, "chunk splits a code fence"


def test_oversized_code_block_is_own_chunk():
    code = "```python\n" + "x = 1\n" * 800 + "```"
    md = f"# H\n\n{para(2)}\n\n{code}\n\n{para(2)}"
    chunks = list(chunk_markdown(md))
    code_chunks = [t for _, t in chunks if t.startswith("```")]
    assert len(code_chunks) == 1
    assert est_tokens(code_chunks[0]) > TARGET_TOKENS


def test_long_section_splits_near_target():
    md = "# H\n\n" + "\n\n".join(para(2) for _ in range(30))
    sizes = [est_tokens(t) for _, t in chunk_markdown(md)]
    assert len(sizes) > 1
    assert all(s <= TARGET_TOKENS * 1.2 for s in sizes)


def test_heading_dense_page_merges_small_sections():
    md = "\n\n".join(f"## Section {i}\n\nOne short line." for i in range(20))
    chunks = list(chunk_markdown(md))
    assert len(chunks) < 10


def test_big_sections_split_at_headings():
    md = f"# A\n\n{para(6)}\n\n# B\n\n{para(6)}"
    chunks = list(chunk_markdown(md))
    assert len(chunks) == 2
    assert chunks[0][0] == "A" and chunks[1][0] == "B"
    assert est_tokens(chunks[0][1]) >= MIN_TOKENS


def test_html_pre_block_preserved_with_lang():
    html = '<p>Intro text.</p><pre><code class="language-sql">SELECT &amp; 1;</code></pre>'
    md = _html_to_markdown(html)
    assert "```sql" in md
    assert "SELECT & 1;" in md


def test_html_headings_and_inline_code():
    html = "<h2>Locking</h2><p>Use <code>SELECT FOR UPDATE</code> here.</p>"
    md = _html_to_markdown(html)
    assert "## Locking" in md
    assert "`SELECT FOR UPDATE`" in md


def test_html_tags_stripped():
    html = '<div class="nav"><span>plain</span> text</div>'
    md = _html_to_markdown(html)
    assert "<" not in md and ">" not in md

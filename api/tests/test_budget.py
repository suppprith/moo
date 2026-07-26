"""Token-budget result shaping."""

from app import budget


def test_estimate_tokens_scales_with_size():
    small = budget.estimate_tokens({"a": "x"})
    big = budget.estimate_tokens({"a": "x" * 4000})
    assert big > small >= 1


def test_clamp_text_truncates_and_marks():
    text = "word " * 500
    out, truncated, total = budget.clamp_text(text, max_tokens=50)
    assert truncated and total == len(text)
    assert len(out) < len(text) and out.endswith("…")
    same, tr, _ = budget.clamp_text("short", 1000)
    assert not tr and same == "short"


def test_pack_keeps_within_budget_and_counts_omitted():
    items = [{"i": i, "pad": "x" * 400} for i in range(10)]
    kept, omitted = budget.pack(items, max_tokens=250)
    assert 1 <= len(kept) < 10
    assert omitted == 10 - len(kept)


def test_pack_always_keeps_at_least_one_large_item():
    kept, omitted = budget.pack([{"pad": "x" * 100000}], max_tokens=1)
    assert len(kept) == 1 and omitted == 0


def test_shape_search_caps_sources_and_keeps_cursor():
    result = {
        "sources": [{"id": f"chk_{i}", "pad": "x" * 400} for i in range(20)],
        "meta": {"page": {"next_cursor": "abc"}},
    }
    shaped = budget.shape_search(result, max_tokens=300)
    assert len(shaped["sources"]) < 20
    assert shaped["truncation"]["sources_omitted"] > 0
    assert shaped["truncation"]["next_cursor"] == "abc"


def test_shape_report_keeps_disputed_points_in_full():
    report = {
        "executive_answer": "answer",
        "disputed_points": [{"text": "big dispute " * 200} for _ in range(5)],
        "open_questions": ["q"],
        "findings": [{"text": "f" * 400} for _ in range(30)],
        "sources": [{"handle": f"doc_{i}", "pad": "x" * 400} for i in range(30)],
    }
    shaped = budget.shape_report(report, max_tokens=500)
    assert len(shaped["disputed_points"]) == 5
    assert len(shaped["findings"]) < 30 or len(shaped["sources"]) < 30
    assert "truncation" in shaped


def test_clamp_row_text_marks_truncation():
    row = {"id": "chk_1", "text": "y" * 10000}
    out = budget.clamp_row_text(row, max_tokens=50)
    assert out["text"].endswith("…")
    assert out["text_truncated"]["original_chars"] == 10000

"""Span highlights + calibrated relevance."""

import pytest

from app.db import migrate
from app.highlights import _spans, highlight_hits
from app.index import vector
from app.websearch import web_search
from tests.test_live_pipeline import DEV_CANDS, PAGES, FakeFetcher, FakeProvider


def test_spans_split_prose_sentences():
    text = ("Autovacuum triggers when dead tuples exceed the threshold. "
            "The scale factor defaults to twenty percent of the table.")
    spans = _spans(text)
    assert len(spans) == 2
    assert spans[0].startswith("Autovacuum")


def test_spans_keep_code_fences_whole():
    text = ("Set the threshold like this.\n\n"
            "```sql\nALTER TABLE t SET (autovacuum_vacuum_scale_factor = 0.05);\n```\n\n"
            "Then reload the configuration for the change to apply.")
    spans = _spans(text)
    assert any(s.startswith("```sql") for s in spans)
    assert not any("```" in s and "ALTER" not in s for s in spans)


def test_spans_skip_tiny_fragments():
    assert _spans("Ok. No. This sentence is long enough to be a real span though.") == [
        "This sentence is long enough to be a real span though."
    ]


ON_TOPIC = ("The autovacuum daemon runs when dead tuples exceed a churn threshold. "
            "Our office coffee machine descaling schedule is posted in the kitchen.")
OFF_TOPIC = ("Sourdough starter needs feeding twice a day in warm weather. "
             "Bulk fermentation takes four to six hours at room temperature.")


def test_highlight_picks_on_topic_sentence():
    got = highlight_hits("when does postgres autovacuum run", [ON_TOPIC], top=1)
    assert "autovacuum" in got[0]["highlights"][0].lower()
    assert "coffee" not in got[0]["highlights"][0].lower()


def test_relevance_calibrated_across_texts():
    got = highlight_hits("when does postgres autovacuum run", [ON_TOPIC, OFF_TOPIC])
    on, off = got[0]["relevance"], got[1]["relevance"]
    assert 0.0 <= off < on <= 1.0
    assert on > 0.6
    assert off < 0.55


def test_empty_texts_safe():
    got = highlight_hits("query", ["", "   "])
    assert got == [{"highlights": [], "relevance": 0.0}] * 2


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "highlights.sqlite"
    migrate(db)
    c = vector.connect(db)
    yield c
    c.close()


def test_web_search_rows_carry_highlights(conn):
    out = web_search(
        conn, "when does postgres autovacuum run", depth="raw",
        live_provider=FakeProvider(DEV_CANDS), live_fetcher=FakeFetcher(PAGES),
    )
    assert out["results"]
    top = out["results"][0]
    assert top["highlights"] and 0.0 < top["relevance"] <= 1.0
    assert "autovacuum" in " ".join(top["highlights"]).lower()


CHANGED = ("To get the current time in UTC, call datetime.now(timezone.utc) from the datetime module. "
           "The result is an aware datetime that carries its time zone with it. "
           "You can format the returned datetime with strftime and an explicit format string. "
           "Note that datetime.utcnow() is deprecated since Python 3.12 and returns a naive value.")


def test_a_span_saying_something_changed_takes_the_last_slot():
    got = highlight_hits("how do I get the current UTC time in Python", [CHANGED], top=2)
    spans = got[0]["highlights"]
    assert any("deprecated" in s for s in spans)
    assert any("now(timezone.utc)" in s for s in spans)


def test_the_lifecycle_swap_leaves_relevance_alone():
    swapped = highlight_hits("how do I get the current UTC time in Python", [CHANGED], top=2)
    single = highlight_hits("how do I get the current UTC time in Python", [CHANGED], top=1)
    assert swapped[0]["relevance"] == single[0]["relevance"]

"""The opt-in usefulness signal: what it stores, what it refuses to store, and
what it does to ranking.

The load-bearing test is `test_ranking_is_untouched_when_nothing_opted_in`: a
self-hosted store that never turned this on must rank exactly as it did before
the feature existed.
"""

import sqlite3

import pytest

from app import feedback, ids
from app.db import migrate
from app.retrieve import RetrievedChunk, fuse


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("MOO_FEEDBACK", "1")
    path = tmp_path / "moo.sqlite"
    migrate(path)
    connection = sqlite3.connect(path, check_same_thread=False)  # TestClient runs off-thread
    connection.row_factory = sqlite3.Row
    connection.execute(
        "INSERT INTO document (id, source_type, url) VALUES (1, 'docs', 'https://sqlite.org/wal')"
    )
    connection.execute(
        "INSERT INTO chunk (id, document_id, ordinal, text) VALUES (7, 1, 0, 'wal mode')"
    )
    connection.commit()
    yield connection
    connection.close()


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MOO_FEEDBACK", raising=False)
    assert feedback.enabled() is False
    monkeypatch.setenv("MOO_FEEDBACK", "1")
    assert feedback.enabled() is True


def test_records_counts_per_handle(conn):
    result = feedback.record(conn, [ids.encode(ids.CHUNK, 7), ids.encode(ids.DOCUMENT, 1)], "cited")
    assert result == {"recorded": 2, "signal": "cited"}

    feedback.record(conn, [ids.encode(ids.CHUNK, 7)], "cited")
    row = conn.execute(
        "SELECT count FROM feedback WHERE kind = 'chk' AND target_id = 7 AND signal = 'cited'"
    ).fetchone()
    assert row["count"] == 2


def test_stores_no_timestamp_finer_than_a_date(conn):
    """A per-event time would let the table be replayed as a session."""
    feedback.record(conn, [ids.encode(ids.CHUNK, 7)], "cited")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(feedback)")}
    assert columns == {"kind", "target_id", "signal", "count", "last_seen"}

    last_seen = conn.execute("SELECT last_seen FROM feedback").fetchone()["last_seen"]
    assert len(last_seen) == len("2026-08-21") and ":" not in last_seen


def test_claim_and_junk_handles_carry_no_signal(conn):
    result = feedback.record(
        conn, [ids.encode(ids.CLAIM, 3), "not-a-handle", ""], "cited"
    )
    assert result["recorded"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM feedback").fetchone()["n"] == 0


def test_unknown_signal_is_rejected(conn):
    with pytest.raises(ValueError, match="unknown signal"):
        feedback.record(conn, [ids.encode(ids.CHUNK, 7)], "thumbs_up")


def test_batch_is_bounded(conn):
    handles = [ids.encode(ids.CHUNK, n) for n in range(feedback.MAX_TARGETS + 25)]
    assert feedback.record(conn, handles, "cited")["recorded"] == feedback.MAX_TARGETS


def test_usefulness_sums_chunk_and_document_signal(conn):
    feedback.record(conn, [ids.encode(ids.CHUNK, 7)], "cited")
    feedback.record(conn, [ids.encode(ids.DOCUMENT, 1)], "cited")
    assert feedback.usefulness(conn, [7], {7: 1})[7] == pytest.approx(2.0)


def test_negative_signal_subtracts(conn):
    feedback.record(conn, [ids.encode(ids.CHUNK, 7)], "cited")
    feedback.record(conn, [ids.encode(ids.CHUNK, 7)], "unhelpful")
    feedback.record(conn, [ids.encode(ids.CHUNK, 7)], "unhelpful")
    assert feedback.usefulness(conn, [7], {})[7] == pytest.approx(-1.0)


def test_usefulness_is_empty_when_capture_is_off(conn, monkeypatch):
    feedback.record(conn, [ids.encode(ids.CHUNK, 7)], "cited")
    monkeypatch.delenv("MOO_FEEDBACK", raising=False)
    assert feedback.usefulness(conn, [7], {}) == {}


def test_factor_is_bounded_and_saturating():
    assert feedback.usefulness_factor(0) == 1.0
    assert feedback.usefulness_factor(None) == 1.0

    small, large, huge = (feedback.usefulness_factor(n) for n in (1, 50, 100_000))
    assert 1.0 < small < large < huge <= 1.0 + feedback.W_USEFULNESS
    assert huge - large < large - small, "must saturate, not grow with volume"

    assert 1.0 - feedback.W_USEFULNESS <= feedback.usefulness_factor(-50) < 1.0


def _hit(**kw) -> RetrievedChunk:
    base = dict(chunk_id=1, score=0.5, text="t", heading=None, url_anchor="u",
                source_type="docs", document_url="https://x", title=None,
                published_at=None, author_role=None, popularity=None, trust_score=0.5)
    base.update(kw)
    return RetrievedChunk(**base)


def test_ranking_is_untouched_when_nothing_opted_in():
    """No signal means the multiplier is exactly 1.0 — not approximately."""
    hit = _hit()
    fuse(hit)
    assert hit.rank_signals["usefulness"] == 1.0
    assert hit.score == 0.5


def test_a_cited_source_outranks_an_identical_one_that_was_not():
    used, unused = _hit(chunk_id=1, usefulness=8.0), _hit(chunk_id=2)
    fuse(used)
    fuse(unused)
    assert used.score > unused.score
    assert used.rank_signals["usefulness"] > 1.0


def test_the_signal_cannot_outweigh_trust():
    """A well-cited weak source must not overtake a strong one on this alone."""
    loved_but_weak = _hit(chunk_id=1, trust_score=0.1, usefulness=10_000)
    plain_but_trusted = _hit(chunk_id=2, trust_score=0.95)
    fuse(loved_but_weak)
    fuse(plain_but_trusted)
    assert plain_but_trusted.score > loved_but_weak.score


class _NoClose:
    """The endpoint closes its connection; the fixture still needs it after."""

    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass


def test_endpoint_is_off_by_default(monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.delenv("MOO_FEEDBACK", raising=False)
    resp = TestClient(main.app).post("/v1/feedback", json={"handles": ["chk_7"]})
    assert resp.status_code == 422
    assert "MOO_FEEDBACK=1" in resp.json()["error"]["message"]


def test_endpoint_records_when_enabled(conn, monkeypatch):
    from fastapi.testclient import TestClient

    from app import main

    monkeypatch.setattr(main, "get_connection", lambda: _NoClose(conn))
    resp = TestClient(main.app).post(
        "/v1/feedback", json={"handles": ["chk_7", "doc_1"], "signal": "helpful"}
    )
    assert resp.status_code == 200
    assert resp.json() == {"recorded": 2, "signal": "helpful"}


def test_mcp_tool_reports_disabled_instead_of_failing(monkeypatch):
    from app.mcp_server import report_useful

    monkeypatch.delenv("MOO_FEEDBACK", raising=False)
    assert report_useful(["chk_7"]) == {"recorded": 0, "signal": "cited", "disabled": True}


def test_stats_and_forget(conn):
    feedback.record(conn, [ids.encode(ids.CHUNK, 7), ids.encode(ids.DOCUMENT, 1)], "cited")
    data = feedback.stats(conn)
    assert data["enabled"] is True
    assert data["totals"]["cited"] == 2
    assert any(t["url"] == "https://sqlite.org/wal" for t in data["targets"])

    assert feedback.forget(conn) == 2
    assert feedback.stats(conn)["targets"] == []

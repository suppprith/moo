"""Unit tests for hybrid retrieval primitives (app.retrieve)."""

from app import retrieve as retrieve_mod
from app.index.keyword import _match_query
from app.retrieve import RRF_K, SIMHASH_HAMMING, _collapse_near_dups, _rrf_merge, hamming, simhash

# -- simhash ---------------------------------------------------------------------

BASE = (
    "Postgres uses multi version concurrency control so readers never block "
    "writers and every update creates a new row version that vacuum later reclaims."
)


def test_simhash_identical_text():
    assert simhash(BASE) == simhash(BASE)


def test_simhash_near_duplicate_within_threshold():
    tweaked = BASE.replace("Postgres", "PostgreSQL") + " Thanks!"
    assert hamming(simhash(BASE), simhash(tweaked)) <= SIMHASH_HAMMING


def test_simhash_different_text_far_apart():
    other = (
        "Redis persistence offers RDB snapshots and the append only file which "
        "fsyncs every second by default trading durability for throughput."
    )
    assert hamming(simhash(BASE), simhash(other)) > SIMHASH_HAMMING


def test_simhash_short_text_no_crash():
    assert isinstance(simhash("hi"), int)
    assert isinstance(simhash(""), int)


# -- near-dup collapse ------------------------------------------------------------

def _rows(items):
    # items: list of (chunk_id, text, canonical_chunk_id)
    return {cid: {"text": t, "canonical_chunk_id": c} for cid, t, c in items}


def test_collapse_folds_near_duplicates():
    dup = BASE.replace("Postgres", "PostgreSQL") + " Thanks!"
    other = "Redis persistence uses RDB snapshots and an append only file for durability."
    rows = _rows([(1, BASE, None), (2, dup, None), (3, other, None)])
    kept = _collapse_near_dups([1, 2, 3], rows)
    # #2 folds into #1 as an alternate; #3 stays its own cluster
    assert [c for c, _ in kept] == [1, 3]
    alts = dict(kept)
    assert alts[1] == [2] and alts[3] == []


def test_collapse_simhashes_each_candidate_once(monkeypatch):
    # regression guard: simhash is O(n) in candidates, not O(n^2) over kept.
    calls = {"n": 0}
    real = retrieve_mod.simhash
    monkeypatch.setattr(retrieve_mod, "simhash", lambda t: (calls.__setitem__("n", calls["n"] + 1), real(t))[1])
    rows = _rows([(i, f"distinct chunk number {i} about topic {i}", None) for i in range(1, 8)])
    _collapse_near_dups(list(rows), rows)
    assert calls["n"] == 7  # one per candidate, never re-hashed per kept cluster


# -- RRF merge --------------------------------------------------------------------

def test_rrf_agreement_wins():
    # id 1 is mid-rank in both lists; id 2 and 3 top one list each
    scores = _rrf_merge([[2, 1, 4], [3, 1, 5]])
    assert scores[1] > scores[4] and scores[1] > scores[5]
    assert scores[1] == 2 / (RRF_K + 2)


def test_rrf_single_list_preserves_order():
    scores = _rrf_merge([[7, 8, 9]])
    assert scores[7] > scores[8] > scores[9]


def test_rrf_boost_multiplies():
    scores = _rrf_merge([[1, 2]], boost={2: 10.0})
    assert scores[2] > scores[1]


# -- keyword OR fallback ------------------------------------------------------------

def test_match_query_or_mode():
    assert _match_query("slow query", operator="OR") == '"slow" OR "query"'
    assert _match_query("slow query") == '"slow" "query"'

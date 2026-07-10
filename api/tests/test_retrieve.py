"""Unit tests for hybrid retrieval primitives (app.retrieve)."""

from app.index.keyword import _match_query
from app.retrieve import RRF_K, SIMHASH_HAMMING, _rrf_merge, hamming, simhash

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

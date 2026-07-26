"""Adaptive query routing."""

import sqlite3

import pytest

from app import search as search_mod
from app.retrieve import _rrf_merge
from app.routing import PARAGRAPH_WORDS, route


def test_identifier_query_routes_exact():
    r = route("what does autovacuum_vacuum_scale_factor control")
    assert r.strategy == "exact" and "identifier" in r.signals
    assert r.keyword_weight > r.vector_weight


def test_error_query_routes_exact():
    r = route("psycopg2 OperationalError SSL SYSCALL error EOF detected")
    assert r.strategy == "exact"
    assert r.keyword_weight > r.vector_weight


def test_quoted_string_routes_exact():
    assert route('fix "too many clients already" postgres').strategy == "exact"


def test_conceptual_query_routes_semantic():
    r = route("why do relational databases prefer write ahead logging over shadow paging")
    assert r.strategy == "semantic"
    assert r.vector_weight > r.keyword_weight


def test_intent_refines_short_query():
    assert route("what is mvcc", intent="definition").strategy == "semantic"
    assert route("redis keys", intent=None).strategy == "balanced"


def test_troubleshooting_without_tokens_stays_balanced():
    r = route("my database keeps running out of connections", intent="troubleshooting")
    assert r.strategy == "balanced" and "troubleshooting" in r.signals


def test_paragraph_query_skips_fan_out():
    long_q = " ".join(["word"] * PARAGRAPH_WORDS) + " postgres"
    r = route(long_q)
    assert r.fan_out is False and "paragraph" in r.signals
    assert route("short query").fan_out is True


def test_rrf_weights_shift_fusion():
    vector_ranking = [1, 2]
    keyword_ranking = [3, 1]
    plain = _rrf_merge([vector_ranking, keyword_ranking])
    assert max(plain, key=plain.get) == 1
    weighted = _rrf_merge([vector_ranking, keyword_ranking], weights=[0.5, 2.0])
    assert weighted[3] > weighted[2]
    assert plain == _rrf_merge([vector_ranking, keyword_ranking], weights=[1.0, 1.0])


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def test_search_passes_weights_and_surfaces_routing(conn, monkeypatch):
    captured = {}

    def fake_retrieve(*a, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(search_mod, "retrieve", fake_retrieve)
    out = search_mod.search(conn, "ERROR: deadlock_detected in postgres", mode="raw", live=False)
    assert captured["index_weights"][1] > captured["index_weights"][0]
    assert out["meta"]["routing"]["strategy"] == "exact"
    assert out["meta"]["routing"]["keyword_weight"] > 1.0


def test_paragraph_query_sends_no_variants(conn, monkeypatch):
    captured = {}

    def fake_retrieve(*a, **kw):
        captured.update(kw)
        return []

    monkeypatch.setattr(search_mod, "retrieve", fake_retrieve)
    long_q = "we keep seeing intermittent failures " * 10
    search_mod.search(conn, long_q, mode="raw", live=False)
    assert captured["queries"] == []

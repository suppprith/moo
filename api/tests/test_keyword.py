"""Unit tests for the FTS5 keyword index helpers."""

from app.index.keyword import _match_query


def test_terms_quoted_for_exact_match():
    assert _match_query("DISTINCT ON") == '"DISTINCT" "ON"'


def test_identifier_with_underscore_survives():
    assert _match_query("array_agg") == '"array_agg"'


def test_fts_operators_neutralized():
    # NEAR/OR/NOT and punctuation must not leak into FTS5 syntax
    q = _match_query('foo" OR bar NEAR(baz)')
    assert '"foo"' in q and '"OR"' in q and '"bar"' in q
    assert q.count('"') % 2 == 0  # balanced quotes -> valid MATCH


def test_empty_and_punctuation_only():
    assert _match_query("") == ""
    assert _match_query("!!! ???") == ""

"""Gold-query evaluation for query understanding.

The 15 gold queries from docs/v1-vertical.md with their expected intents;
queries the doc labels with dual intents accept either.
"""

import pytest

from app.understand import classify_intent, extract_entities, understand

GOLD = [
    ("Why is my Postgres query not using the index I created?", {"troubleshooting"}),
    ("Postgres vs MySQL for a new web app in 2024", {"comparison"}),
    ("Should I use JSONB or a normalized schema in Postgres?", {"comparison"}),
    ("Why do my database connections keep getting exhausted?", {"troubleshooting"}),
    ("What is the difference between VARCHAR and TEXT in Postgres?", {"definition"}),
    ("How do I fix a deadlock in MySQL/Postgres?", {"how-to"}),
    ("Is SQLite good enough for production?", {"why", "comparison"}),
    ("Redis vs Postgres for a job/task queue", {"comparison"}),
    ("Why is autovacuum causing performance problems / not keeping up?", {"troubleshooting"}),
    ("What isolation level should I use and what are the anomalies?", {"definition", "why"}),
    ("How does MVCC work in Postgres?", {"definition"}),
    ("When should I add an index and when does it hurt?", {"why"}),
    ("Why did my BIGINT/INT primary key run out / overflow?", {"troubleshooting"}),
    ("RDB vs AOF: how should I configure Redis persistence?", {"how-to", "comparison"}),
    ("How do I make a slow SELECT COUNT(*) faster in Postgres?", {"how-to"}),
]


@pytest.mark.parametrize("query,expected", GOLD, ids=[q[:40] for q, _ in GOLD])
def test_gold_query_intent(query, expected):
    assert classify_intent(query) in expected


def test_entity_extraction_with_aliases():
    assert extract_entities("Postgres vs MySQL for a new web app") == ["PostgreSQL", "MySQL"]
    assert extract_entities("pg autovacuum tuning") == ["PostgreSQL", "VACUUM"]
    assert extract_entities("kubernetes ingress") == []


def test_understanding_drives_downstream_behavior():
    trouble = understand("Why do my database connections keep getting exhausted?")
    assert trouble.source_boost.get("github_issue", 1) > 1
    assert not trouble.contradiction_view

    comp = understand("Redis vs Postgres for a job queue")
    assert comp.contradiction_view
    assert comp.entities == ["Redis", "PostgreSQL"]


def test_ambiguity_flag():
    assert understand("db slow").ambiguous
    assert not understand("redis slow").ambiguous

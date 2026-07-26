"""Query understanding: intent + entities + ambiguity.

``understand(query)`` classifies the query *before* retrieval:

- **intent** — how-to / why / comparison / troubleshooting / definition, via a
  deterministic rule cascade: free, fast, and testable against the gold queries.
- **entities** — canonical names matched against the ``entity`` table when
  populated, falling back to a built-in alias map.
- **ambiguous** — too little signal to retrieve confidently.

Intent drives downstream behavior: each intent maps to a ``source_boost``
(fed to ``retrieve()``) — troubleshooting weights issues/SO up, definition
weights official docs up — and comparisons additionally flag the
contradiction view for the evidence layer.

CLI:  ``uv run python -m app.understand "why is my query slow"``
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from dataclasses import dataclass, field

INTENTS = ("how-to", "why", "comparison", "troubleshooting", "definition")

BUILTIN_ALIASES: dict[str, str] = {
    "postgres": "PostgreSQL", "postgresql": "PostgreSQL", "pg": "PostgreSQL",
    "mysql": "MySQL", "mariadb": "MariaDB", "innodb": "MySQL",
    "sqlite": "SQLite", "sqlite3": "SQLite",
    "redis": "Redis",
    "pgbouncer": "PgBouncer",
    "jsonb": "JSONB", "mvcc": "MVCC", "wal": "WAL",
    "vacuum": "VACUUM", "autovacuum": "VACUUM",
    "rdb": "Redis RDB", "aof": "Redis AOF",
}

_TROUBLE_SIGNALS = re.compile(
    r"\bnot\b|\bkeeps?\s+(getting|failing|dying|crashing)\b|\bexhausted\b|"
    r"\boverflow\b|\brun\s+out\b|\bcrash|\bfail|\bbroken\b|\bstuck\b|\bleak|"
    r"\berror\b|\bproblems?\b|\btimeout\b|\bhanging\b",
    re.I,
)
_COMPARISON_SIGNALS = re.compile(
    r"\bvs\.?\b|\bversus\b|\bdifference between\b|\bbetter\b|\bgood enough\b|"
    r"\bshould i (use|pick|choose)\b.*\bor\b|\bcompared? (to|with)\b",
    re.I,
)

BOOSTS: dict[str, dict[str, float]] = {
    "troubleshooting": {
        "github_issue": 1.4, "github_comment": 1.25, "so_question": 1.3, "so_answer": 1.3,
    },
    "comparison": {"hn_story": 1.2, "blog": 1.2, "reddit_post": 1.15},
    "how-to": {"docs": 1.3, "so_answer": 1.2},
    "definition": {"docs": 1.4, "blog": 1.2},
    "why": {"blog": 1.2, "docs": 1.2, "hn_story": 1.1},
}


@dataclass
class QueryUnderstanding:
    intent: str
    entities: list[str]
    ambiguous: bool
    contradiction_view: bool = False
    source_boost: dict[str, float] = field(default_factory=dict)


def classify_intent(query: str) -> str:
    q = query.strip().lower()
    if re.match(r"^what\b", q) or re.match(r"^how (does|do)\b.*\bwork", q):
        return "definition"
    if re.match(r"^how (do|can|should|would) i\b", q) or re.match(r"^how to\b", q):
        return "how-to"
    if _TROUBLE_SIGNALS.search(q):
        return "troubleshooting"
    if _COMPARISON_SIGNALS.search(q):
        return "comparison"
    if re.match(r"^(why|when should)\b", q):
        return "why"
    return "how-to"


def extract_entities(query: str, conn: sqlite3.Connection | None = None) -> list[str]:
    aliases = dict(BUILTIN_ALIASES)
    if conn is not None:
        try:
            rows = conn.execute(
                "SELECT ea.alias, e.canonical_name FROM entity_alias ea "
                "JOIN entity e ON e.id = ea.entity_id"
            ).fetchall()
            aliases.update({r[0].lower(): r[1] for r in rows})
        except sqlite3.Error:
            pass
    found: list[str] = []
    for word in re.findall(r"\w+", query.lower()):
        canon = aliases.get(word)
        if canon and canon not in found:
            found.append(canon)
    return found


def understand(query: str, conn: sqlite3.Connection | None = None) -> QueryUnderstanding:
    intent = classify_intent(query)
    entities = extract_entities(query, conn)
    content_words = [w for w in re.findall(r"\w+", query) if len(w) > 2]
    ambiguous = not entities and len(content_words) <= 3
    return QueryUnderstanding(
        intent=intent,
        entities=entities,
        ambiguous=ambiguous,
        contradiction_view=(intent == "comparison"),
        source_boost=dict(BOOSTS.get(intent, {})),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.understand", description="Classify a query")
    parser.add_argument("query")
    parser.add_argument("--show-retrieval", action="store_true",
                        help="show boosted vs unboosted top results")
    args = parser.parse_args(argv)

    from .db import get_connection

    conn = get_connection()
    u = understand(args.query, conn)
    print(json.dumps(u.__dict__, indent=2))

    if args.show_retrieval:
        from .index.vector import connect
        from .retrieve import retrieve

        conn.close()
        conn = connect()
        plain = retrieve(conn, args.query, k=5)
        boosted = retrieve(conn, args.query, k=5, source_boost=u.source_boost)
        print("--- unboosted ---")
        for h in plain:
            print(f"  #{h.chunk_id:<5} {h.source_type}")
        print(f"--- boosted for {u.intent} ---")
        for h in boosted:
            print(f"  #{h.chunk_id:<5} {h.source_type}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

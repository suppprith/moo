"""SQLite connection + migration runner for moo search.

Stdlib-only (``sqlite3``); no ORM. Migrations are plain ``.sql`` files in
``api/migrations/`` applied in filename order and recorded in ``schema_migrations``.

Apply migrations:  ``uv run python -m app.db``
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = API_DIR / "migrations"
DEFAULT_DB_PATH = API_DIR / "data" / "moo.sqlite"


def get_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open a connection with foreign keys + WAL enabled, creating parent dirs."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _ensure_migrations_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )


def migrate(db_path: Path | str = DEFAULT_DB_PATH) -> int:
    """Apply any pending migrations. Returns the number applied."""
    conn = get_connection(db_path)
    try:
        _ensure_migrations_table(conn)
        applied = {row["version"] for row in conn.execute("SELECT version FROM schema_migrations")}
        pending = [f for f in sorted(MIGRATIONS_DIR.glob("*.sql")) if f.stem not in applied]
        for f in pending:
            conn.executescript(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (f.stem,))
            conn.commit()
            print(f"applied {f.name}")
        if not pending:
            print("no pending migrations")
        return len(pending)
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()

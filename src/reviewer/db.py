"""SQLite persistence — stdlib sqlite3, no ORM.

Durable state is what unlocks incremental review and cross-run dedup
(v3 kept everything in memory and lost it on restart).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("REVIEWER_DB", ROOT / "reviewer.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS review (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    change_id     TEXT NOT NULL,
    patchset      INTEGER NOT NULL,
    project_slug  TEXT NOT NULL DEFAULT '',
    branch        TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending|done|error
    files_reviewed INTEGER NOT NULL DEFAULT 0,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0.0,
    error         TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(change_id, patchset)
);

CREATE TABLE IF NOT EXISTS comment (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id     INTEGER NOT NULL REFERENCES review(id) ON DELETE CASCADE,
    file          TEXT NOT NULL,
    line          INTEGER NOT NULL DEFAULT 0,
    severity      TEXT NOT NULL DEFAULT 'suggestion',
    comment       TEXT NOT NULL DEFAULT '',
    existing_code TEXT NOT NULL DEFAULT '',
    suggestion_code TEXT NOT NULL DEFAULT '',
    posted        INTEGER NOT NULL DEFAULT 0,
    feedback      TEXT NOT NULL DEFAULT ''  -- ''|useful|dismissed (rule-effectiveness signal)
);

CREATE INDEX IF NOT EXISTS idx_comment_review ON comment(review_id);
CREATE INDEX IF NOT EXISTS idx_review_change ON review(change_id);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """Apply schema. Opens the default DB when no connection is given."""
    conn = conn or connect(DB_PATH)
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Idempotent column adds for DBs created before a column existed.

    ponytail: SQLite has no ADD COLUMN IF NOT EXISTS, so check the table info.
    Cheap enough to run every boot; no migration framework for one column.
    """
    comment_cols = {r["name"] for r in conn.execute("PRAGMA table_info(comment)")}
    if "feedback" not in comment_cols:
        conn.execute("ALTER TABLE comment ADD COLUMN feedback TEXT NOT NULL DEFAULT ''")
    review_cols = {r["name"] for r in conn.execute("PRAGMA table_info(review)")}
    for column in ("input_tokens", "output_tokens"):
        if column not in review_cols:
            conn.execute(f"ALTER TABLE review ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0")


if __name__ == "__main__":
    # ponytail: self-check schema applies and the unique constraint holds.
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        c = connect(Path(d) / "t.db")
        try:
            init_db(c)
            c.execute("INSERT INTO review(change_id, patchset) VALUES('1', 1)")
            c.commit()
            try:
                c.execute("INSERT INTO review(change_id, patchset) VALUES('1', 1)")
                c.commit()
                raise SystemExit("FAIL: duplicate (change_id, patchset) allowed")
            except sqlite3.IntegrityError:
                pass
        finally:
            c.close()  # ponytail: WAL file stays locked on Windows until closed
    print("db.py self-check OK")

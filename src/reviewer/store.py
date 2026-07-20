"""Persistence for reviews + comments. Raw SQL over the db.py schema.

ponytail: thin functions, not a repository class. A review is a row plus its
comment rows; two inserts and two selects cover the whole Phase-2 need.
"""
from __future__ import annotations

import sqlite3

from .models import ReviewComment, TokenUsage


def start_review(
    conn: sqlite3.Connection,
    change_id: str,
    patchset: int,
    project_slug: str = "",
    branch: str = "",
) -> int:
    """Create (or reset) the review row for a (change, patchset). Returns its id.

    Re-reviewing a patchset replaces the prior row's comments — the UNIQUE
    (change_id, patchset) constraint makes this an upsert.
    """
    cur = conn.execute(
        "INSERT INTO review(change_id, patchset, project_slug, branch, status) "
        "VALUES(?,?,?,?,'pending') "
        "ON CONFLICT(change_id, patchset) DO UPDATE SET "
        "project_slug=excluded.project_slug, branch=excluded.branch, "
        "status='pending', error='', files_reviewed=0, cost_usd=0.0",
        (change_id, patchset, project_slug, branch),
    )
    conn.commit()
    row = conn.execute(
        "SELECT id FROM review WHERE change_id=? AND patchset=?",
        (change_id, patchset),
    ).fetchone()
    review_id = row["id"]
    # Clear any stale comments from a previous run of this patchset.
    conn.execute("DELETE FROM comment WHERE review_id=?", (review_id,))
    conn.commit()
    return review_id


def finish_review(
    conn: sqlite3.Connection,
    review_id: int,
    comments: list[ReviewComment],
    usage: TokenUsage,
    status: str = "done",
    error: str = "",
) -> None:
    conn.executemany(
        "INSERT INTO comment(review_id, file, line, severity, comment, "
        "existing_code, suggestion_code) VALUES(?,?,?,?,?,?,?)",
        [
            (review_id, c.file, c.line, c.severity, c.comment,
             c.existing_code, c.suggestion_code)
            for c in comments
        ],
    )
    conn.execute(
        "UPDATE review SET status=?, error=?, files_reviewed=?, input_tokens=?, "
        "output_tokens=?, cost_usd=? WHERE id=?",
        (status, error, usage.files_reviewed, usage.input_tokens,
         usage.output_tokens, usage.cost_usd, review_id),
    )
    conn.commit()


def get_review(conn: sqlite3.Connection, change_id: str, patchset: int) -> dict | None:
    row = conn.execute(
        "SELECT * FROM review WHERE change_id=? AND patchset=?",
        (change_id, patchset),
    ).fetchone()
    if not row:
        return None
    comments = conn.execute(
        "SELECT id, file, line, severity, comment, existing_code, suggestion_code, "
        "posted, feedback FROM comment WHERE review_id=? ORDER BY file, line",
        (row["id"],),
    ).fetchall()
    return {**dict(row), "comments": [dict(c) for c in comments]}


def get_latest_review(conn: sqlite3.Connection, change_id: str) -> dict | None:
    """Most recent patchset's review for a change (the one a UI wants by default)."""
    row = conn.execute(
        "SELECT patchset FROM review WHERE change_id=? ORDER BY patchset DESC LIMIT 1",
        (change_id,),
    ).fetchone()
    return get_review(conn, change_id, row["patchset"]) if row else None


def last_reviewed_patchset(conn: sqlite3.Connection, change_id: str, before: int) -> int:
    """Highest done patchset strictly below `before` — the incremental base.

    Returns 0 when there's no earlier completed review (→ full review).
    """
    row = conn.execute(
        "SELECT patchset FROM review WHERE change_id=? AND patchset<? AND status='done' "
        "ORDER BY patchset DESC LIMIT 1",
        (change_id, before),
    ).fetchone()
    return row["patchset"] if row else 0


def mark_posted(
    conn: sqlite3.Connection, review_id: int, comment_ids: list[int] | None = None
) -> None:
    """Mark comments posted. All of the review's comments, or just `comment_ids`."""
    if comment_ids is None:
        conn.execute("UPDATE comment SET posted=1 WHERE review_id=?", (review_id,))
    elif comment_ids:
        marks = ",".join("?" * len(comment_ids))
        conn.execute(
            f"UPDATE comment SET posted=1 WHERE review_id=? AND id IN ({marks})",
            (review_id, *comment_ids),
        )
    conn.commit()


def update_comment_text(conn: sqlite3.Connection, comment_id: int, comment: str) -> bool:
    """Edit a comment's text before it's posted. Returns True if a row changed."""
    text = comment.strip()
    if not text:
        raise ValueError("comment text must not be empty")
    cur = conn.execute(
        "UPDATE comment SET comment=? WHERE id=? AND posted=0", (text, comment_id)
    )
    conn.commit()
    return cur.rowcount > 0


def review_history(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Recent reviews (newest first) with a comment count — for the history view."""
    rows = conn.execute(
        "SELECT r.id, r.change_id, r.patchset, r.project_slug, r.branch, r.status, "
        "r.files_reviewed, r.input_tokens, r.output_tokens, r.cost_usd, r.created_at, "
        "COUNT(c.id) AS comment_count "
        "FROM review r LEFT JOIN comment c ON c.review_id=r.id "
        "GROUP BY r.id ORDER BY r.created_at DESC, r.id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [dict(r) for r in rows]


def set_feedback(conn: sqlite3.Connection, comment_id: int, feedback: str) -> bool:
    """Record a useful/dismissed signal on one comment. Returns True if a row changed."""
    if feedback not in ("", "useful", "dismissed"):
        raise ValueError("feedback must be '', 'useful', or 'dismissed'")
    cur = conn.execute("UPDATE comment SET feedback=? WHERE id=?", (feedback, comment_id))
    conn.commit()
    return cur.rowcount > 0


def rule_effectiveness(conn: sqlite3.Connection) -> list[dict]:
    """Per-severity useful/dismissed tallies — surfaces noisy rules (v3 lacked this)."""
    rows = conn.execute(
        "SELECT severity, "
        "SUM(feedback='useful') AS useful, "
        "SUM(feedback='dismissed') AS dismissed, "
        "COUNT(*) AS total "
        "FROM comment GROUP BY severity ORDER BY severity",
    ).fetchall()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    # ponytail: self-check the upsert-resets-comments contract.
    import tempfile
    from pathlib import Path
    from .db import connect, init_db

    with tempfile.TemporaryDirectory() as d:
        c = connect(Path(d) / "t.db")
        try:
            init_db(c)
            rid = start_review(c, "42", 1, "proj", "main")
            finish_review(
                c, rid,
                [ReviewComment(file="a.py", line=1, comment="x")],
                TokenUsage(files_reviewed=1, cost_usd=0.01),
            )
            got = get_review(c, "42", 1)
            assert got["status"] == "done" and len(got["comments"]) == 1

            # Re-review same patchset → old comment gone, new one persisted.
            rid2 = start_review(c, "42", 1)
            assert rid2 == rid, "same patchset must reuse the review row"
            finish_review(
                c, rid2,
                [ReviewComment(file="b.py", line=2, comment="y")],
                TokenUsage(files_reviewed=1),
            )
            got = get_review(c, "42", 1)
            assert len(got["comments"]) == 1 and got["comments"][0]["file"] == "b.py"
        finally:
            c.close()
    print("store.py self-check OK")

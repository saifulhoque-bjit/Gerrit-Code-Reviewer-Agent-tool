"""Phase 4: native Gerrit output — payload builders (pure), incremental
review, dedup-against-posted, auto-vote. Network stays faked."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from reviewer.db import connect, init_db
from reviewer.engine import post_to_gerrit, run_review
from reviewer.gerrit import build_robot_comments, posted_comment_keys, vote_label
from reviewer.models import ChangedFile, ReviewComment
from reviewer.settings import get_settings


# ── pure payload builders ─────────────────────────────────────────
def test_build_robot_comments_groups_by_file_with_ids():
    cs = [ReviewComment(file="a.py", line=3, severity="error", comment="boom"),
          ReviewComment(file="a.py", line=9, severity="warning", comment="hmm")]
    out = build_robot_comments(cs, run_id="42-ps2")
    assert set(out) == {"a.py"}
    assert len(out["a.py"]) == 2
    e = out["a.py"][0]
    assert e["robot_id"] == "hermes-reviewer" and e["robot_run_id"] == "42-ps2"
    assert e["line"] == 3 and "[ERROR]" in e["message"]


def test_build_robot_comments_adds_fix_suggestion():
    cs = [ReviewComment(file="a.py", line=5, comment="use x",
                        suggestion_code="x = 1")]
    e = build_robot_comments(cs, "r1")["a.py"][0]
    fix = e["fix_suggestions"][0]
    assert fix["replacements"][0]["replacement"] == "x = 1"
    assert fix["replacements"][0]["path"] == "a.py"


def test_build_robot_comments_no_fix_when_no_suggestion():
    cs = [ReviewComment(file="a.py", line=5, comment="just fyi")]
    assert "fix_suggestions" not in build_robot_comments(cs, "r1")["a.py"][0]


def test_posted_comment_keys_extracts_file_line():
    robot = {"a.py": [{"line": 3, "message": "x"}, {"line": 9, "message": "y"}],
             "b.py": [{"line": 1, "message": "z"}]}
    assert posted_comment_keys(robot) == {("a.py", 3), ("a.py", 9), ("b.py", 1)}


def test_vote_label_error_gives_minus_one():
    cs = [ReviewComment(file="a", line=1, severity="error", comment="x")]
    assert vote_label(cs, enabled=True) == {"Code-Review": -1}


def test_vote_label_warning_only_gives_zero():
    cs = [ReviewComment(file="a", line=1, severity="warning", comment="x")]
    assert vote_label(cs, enabled=True) == {"Code-Review": 0}


def test_vote_label_disabled_is_empty():
    cs = [ReviewComment(file="a", line=1, severity="error", comment="x")]
    assert vote_label(cs, enabled=False) == {}


# ── incremental review ────────────────────────────────────────────
class RecordingGerrit:
    """Fake that records the `base` it was asked for."""
    def __init__(self):
        self.base_seen = "unset"

    async def change_detail(self, change_id):
        return {"branch": "main", "current_revision": "r2",
                "revisions": {"r2": {"_number": 2}}}

    async def changed_files(self, change_id, base=None):
        self.base_seen = base
        return [ChangedFile(path="a.py", lines_inserted=4)]

    async def file_diff(self, change_id, file_path):
        return f"--- a/{file_path}\n+new"


async def _worker(path, diff, rules, slug, mcp, cap, timeout):
    return [ReviewComment(file=path, line=1, comment="issue")]


@pytest.mark.asyncio
async def test_incremental_passes_prior_patchset_as_base():
    with tempfile.TemporaryDirectory() as d:
        conn = connect(Path(d) / "t.db")
        try:
            init_db(conn)
            g = RecordingGerrit()
            # First review at ps2 → no prior → base None (full review).
            await run_review(conn, get_settings(), g, _worker,
                             change_id="800", auth="x", rules_dir=Path(d) / "r")
            assert g.base_seen is None

            # Simulate a later patchset: ps3 should use ps2 as incremental base.
            class G3(RecordingGerrit):
                async def change_detail(self, change_id):
                    return {"branch": "main", "current_revision": "r3",
                            "revisions": {"r3": {"_number": 3}}}
            g3 = G3()
            await run_review(conn, get_settings(), g3, _worker,
                             change_id="800", auth="x", rules_dir=Path(d) / "r")
            assert g3.base_seen == 2  # prior reviewed patchset
        finally:
            conn.close()


# ── post-back with dedup-against-posted ───────────────────────────
class PostGerrit:
    def __init__(self, already: dict | None = None):
        self.already = already or {}
        self.posted_payload = None

    async def list_robot_comments(self, change_id):
        return self.already

    async def post_review(self, change_id, robot_comments, message="", labels=None):
        self.posted_payload = {"robot": robot_comments, "message": message, "labels": labels}
        return 200


async def _seed_review(conn, change_id, patchset, comments):
    from reviewer.store import start_review, finish_review
    from reviewer.models import TokenUsage
    rid = start_review(conn, change_id, patchset)
    finish_review(conn, rid, comments, TokenUsage(files_reviewed=1), status="done")


@pytest.mark.asyncio
async def test_post_dedups_against_already_posted():
    with tempfile.TemporaryDirectory() as d:
        conn = connect(Path(d) / "t.db")
        try:
            init_db(conn)
            await _seed_review(conn, "900", 1, [
                ReviewComment(file="a.py", line=3, severity="error", comment="x"),
                ReviewComment(file="a.py", line=9, severity="warning", comment="y"),
            ])
            # Gerrit already has the line-3 comment → only line-9 should post.
            g = PostGerrit(already={"a.py": [{"line": 3, "message": "old"}]})
            res = await post_to_gerrit(conn, get_settings(), g, "900", 1)
            assert res["posted"] == 1 and res["skipped"] == 1
            assert list(g.posted_payload["robot"]["a.py"][0].keys())  # payload built
            assert g.posted_payload["robot"]["a.py"][0]["line"] == 9
        finally:
            conn.close()


@pytest.mark.asyncio
async def test_post_all_already_posted_is_noop():
    with tempfile.TemporaryDirectory() as d:
        conn = connect(Path(d) / "t.db")
        try:
            init_db(conn)
            await _seed_review(conn, "901", 1,
                               [ReviewComment(file="a.py", line=3, comment="x")])
            g = PostGerrit(already={"a.py": [{"line": 3, "message": "old"}]})
            res = await post_to_gerrit(conn, get_settings(), g, "901", 1)
            assert res["posted"] == 0 and g.posted_payload is None
        finally:
            conn.close()

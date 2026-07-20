"""Phase 2: parser, store roundtrip, and engine fan-out — all without a real
hermes subprocess or network (worker + gerrit are fakes)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from reviewer.db import connect, init_db
from reviewer.engine import run_review
from reviewer.models import ChangedFile, ReviewComment
from reviewer.parser import parse_comments
from reviewer.settings import get_settings
from reviewer.store import get_review, start_review, finish_review
from reviewer.models import TokenUsage


# ── parser ────────────────────────────────────────────────────────
def test_parser_direct_json():
    out = parse_comments('[{"file":"a.py","line":3,"severity":"error","comment":"boom"}]')
    assert len(out) == 1 and out[0].severity == "error" and out[0].line == 3


def test_parser_code_fence():
    raw = 'Here you go:\n```json\n[{"file":"a.py","line":1,"comment":"x"}]\n```\ndone'
    out = parse_comments(raw)
    assert len(out) == 1 and out[0].file == "a.py"


def test_parser_bracket_counting_picks_longest():
    raw = 'noise [1,2] more [{"file":"a","line":1,"comment":"c"}] tail'
    out = parse_comments(raw)
    assert len(out) == 1 and out[0].comment == "c"


def test_parser_prose_returns_empty():
    assert parse_comments("No issues found. LGTM!") == []


def test_parser_bad_severity_normalized():
    out = parse_comments('[{"file":"a","line":1,"severity":"nitpick","comment":"c"}]')
    assert out[0].severity == "suggestion"


# ── store roundtrip ───────────────────────────────────────────────
def test_store_roundtrip_and_patchset_upsert():
    with tempfile.TemporaryDirectory() as d:
        conn = connect(Path(d) / "t.db")
        try:
            init_db(conn)
            rid = start_review(conn, "500", 1, "proj", "main")
            finish_review(conn, rid, [ReviewComment(file="a.py", line=1, comment="x")],
                          TokenUsage(files_reviewed=1, cost_usd=0.02))
            got = get_review(conn, "500", 1)
            assert got["status"] == "done" and len(got["comments"]) == 1
            # Re-review same patchset replaces comments.
            rid2 = start_review(conn, "500", 1)
            assert rid2 == rid
            finish_review(conn, rid2, [ReviewComment(file="b.py", line=2, comment="y")],
                          TokenUsage(files_reviewed=1))
            got = get_review(conn, "500", 1)
            assert len(got["comments"]) == 1 and got["comments"][0]["file"] == "b.py"
        finally:
            conn.close()


# ── engine fan-out (fakes) ────────────────────────────────────────
class FakeGerrit:
    async def change_detail(self, change_id):
        return {"branch": "main", "current_revision": "rev1",
                "revisions": {"rev1": {"_number": 2}}}

    async def changed_files(self, change_id, base=None):
        return [ChangedFile(path="a.py", lines_inserted=5),
                ChangedFile(path="b.py", lines_inserted=3),
                ChangedFile(path="unchanged.py")]  # no changes → skipped

    async def file_diff(self, change_id, file_path):
        return f"--- a/{file_path}\n+++ b/{file_path}\n+new line"


async def fake_worker(path, diff, rule_paths, slug, mcp_config, cap, timeout, *, branch=""):
    # One comment per file; a.py gets a duplicate to exercise dedup.
    if path == "a.py":
        return [ReviewComment(file="a.py", line=1, comment="issue"),
                ReviewComment(file="a.py", line=1, comment="dup — same key")]
    return [ReviewComment(file=path, line=2, comment="issue")]


@pytest.mark.asyncio
async def test_engine_end_to_end_with_fakes():
    with tempfile.TemporaryDirectory() as d:
        conn = connect(Path(d) / "t.db")
        try:
            init_db(conn)
            result = await run_review(
                conn, get_settings(), FakeGerrit(), fake_worker,
                change_id="500", auth="Basic x", project_slug="proj",
                rules_dir=Path(d) / "rules",  # empty → no rule files, fine
            )
            assert result["status"] == "done"
            assert result["patchset"] == 2
            # 2 changed files reviewed (unchanged.py skipped)
            assert result["files_reviewed"] == 2
            # a.py's duplicate (same file+line) deduped → 2 comments total
            assert len(result["comments"]) == 2
            files = {c["file"] for c in result["comments"]}
            assert files == {"a.py", "b.py"}
        finally:
            conn.close()


@pytest.mark.asyncio
async def test_engine_no_changed_files():
    class EmptyGerrit(FakeGerrit):
        async def changed_files(self, change_id, base=None):
            return [ChangedFile(path="x.py")]  # zero line changes

    with tempfile.TemporaryDirectory() as d:
        conn = connect(Path(d) / "t.db")
        try:
            init_db(conn)
            result = await run_review(
                conn, get_settings(), EmptyGerrit(), fake_worker,
                change_id="501", auth="Basic x", rules_dir=Path(d) / "rules",
            )
            assert result["status"] == "done" and result["comments"] == []
        finally:
            conn.close()

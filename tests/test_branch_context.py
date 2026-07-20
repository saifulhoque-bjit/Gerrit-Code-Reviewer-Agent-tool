"""Phase 4: branch-aware context. The change's target branch must flow into
the MCP runtime config and the worker prompt so cross-file lookups resolve
against the right revision. Network + hermes stay faked."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from reviewer.db import connect, init_db
from reviewer.engine import _write_mcp_config, run_review
from reviewer.models import ChangedFile, ReviewComment
from reviewer.settings import get_settings


# ── _write_mcp_config carries the branch ──────────────────────────
def test_mcp_config_includes_branch(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # temp/ is created relative to cwd
    path = _write_mcp_config(get_settings(), "123", "Basic x", "", "release-2.1")
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["branch"] == "release-2.1"
    assert data["change_id"] == "123"


def test_mcp_config_branch_defaults_empty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write_mcp_config(get_settings(), "123", "Basic x", "")
    assert json.loads(Path(path).read_text(encoding="utf-8"))["branch"] == ""


# ── branch reaches the worker through run_review ──────────────────
class BranchGerrit:
    def __init__(self, branch):
        self._branch = branch

    async def change_detail(self, change_id):
        return {"branch": self._branch, "current_revision": "r1",
                "revisions": {"r1": {"_number": 1}}}

    async def changed_files(self, change_id, base=None):
        return [ChangedFile(path="a.py", lines_inserted=4)]

    async def file_diff(self, change_id, file_path):
        return f"--- a/{file_path}\n+new"


@pytest.mark.asyncio
async def test_branch_is_passed_to_worker():
    seen = {}

    async def capturing_worker(path, diff, rules, slug, mcp, cap, timeout, *, branch=""):
        seen["branch"] = branch
        return [ReviewComment(file=path, line=1, comment="x")]

    with tempfile.TemporaryDirectory() as d:
        conn = connect(Path(d) / "t.db")
        try:
            init_db(conn)
            await run_review(conn, get_settings(), BranchGerrit("feature/login"),
                             capturing_worker, change_id="700", auth="x",
                             rules_dir=Path(d) / "rules")
            assert seen["branch"] == "feature/login"
        finally:
            conn.close()


@pytest.mark.asyncio
async def test_branch_written_to_mcp_config_during_review(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    async def noop_worker(path, diff, rules, slug, mcp, cap, timeout, *, branch=""):
        return []

    conn = connect(tmp_path / "t.db")
    try:
        init_db(conn)
        await run_review(conn, get_settings(), BranchGerrit("stable"),
                         noop_worker, change_id="701", auth="Basic z",
                         rules_dir=tmp_path / "rules")
        cfg = json.loads((tmp_path / "temp" / "mcp_runtime.json").read_text(encoding="utf-8"))
        assert cfg["branch"] == "stable"
    finally:
        conn.close()


# ── worker prompts embed the branch (fallback to 'unknown') ───────
def test_strategy_a_prompt_includes_branch():
    from reviewer.worker import PROMPT_TEMPLATE
    out = PROMPT_TEMPLATE.format(
        file_path="a.py", project_slug="p", branch="release-9",
        rules_files="r.md", diff="d")
    assert "Target branch: release-9" in out


def test_strategy_b_prompt_includes_branch():
    from reviewer.worker_direct import PROMPT_TEMPLATE
    out = PROMPT_TEMPLATE.format(
        file_path="a.py", project_slug="p", branch="release-9", diff="d")
    assert "Target branch: release-9" in out

"""Phase 3: event bus pub/sub + engine emits streaming events (fakes)."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from reviewer.db import connect, init_db
from reviewer.engine import run_review
from reviewer.events import EventBus
from reviewer.models import ChangedFile, ReviewComment
from reviewer.settings import get_settings


# ── event bus ─────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_bus_delivers_to_subscribers():
    bus = EventBus()
    q1 = bus.subscribe("42")
    q2 = bus.subscribe("42")
    await bus.publish("42", {"type": "file", "file": "a.py"})
    assert (await q1.get())["file"] == "a.py"
    assert (await q2.get())["file"] == "a.py"


@pytest.mark.asyncio
async def test_bus_isolates_changes_and_unsubscribe():
    bus = EventBus()
    q = bus.subscribe("42")
    await bus.publish("99", {"type": "file"})  # different change → not delivered
    assert q.empty()
    bus.unsubscribe("42", q)
    await bus.publish("42", {"type": "file"})  # no subscribers left → no error
    assert q.empty()


# ── engine emits events ───────────────────────────────────────────
class FakeGerrit:
    async def change_detail(self, change_id):
        return {"branch": "main", "current_revision": "r1", "revisions": {"r1": {"_number": 3}}}

    async def changed_files(self, change_id, base=None):
        return [ChangedFile(path="a.py", lines_inserted=5),
                ChangedFile(path="b.py", lines_inserted=2)]

    async def file_diff(self, change_id, file_path):
        return f"--- a/{file_path}\n+new"


async def fake_worker(path, diff, rules, slug, mcp, cap, timeout):
    return [ReviewComment(file=path, line=1, comment="issue")]


@pytest.mark.asyncio
async def test_engine_emits_started_file_done_events():
    events = []

    async def collect(evt):
        events.append(evt)

    with tempfile.TemporaryDirectory() as d:
        conn = connect(Path(d) / "t.db")
        try:
            init_db(conn)
            await run_review(
                conn, get_settings(), FakeGerrit(), fake_worker,
                change_id="700", auth="Basic x", rules_dir=Path(d) / "rules",
                on_event=collect,
            )
        finally:
            conn.close()

    types = [e["type"] for e in events]
    assert types[0] == "started"
    assert types.count("file") == 2  # one per changed file
    assert types[-1] == "done"
    started = next(e for e in events if e["type"] == "started")
    assert started["total_files"] == 2 and started["patchset"] == 3
    done = events[-1]
    assert done["status"] == "done" and len(done["comments"]) == 2

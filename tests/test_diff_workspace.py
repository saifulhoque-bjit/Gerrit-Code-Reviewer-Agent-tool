"""Authenticated Gerrit diff-workspace routes."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


def test_change_file_and_diff_routes_use_authenticated_gerrit_client(monkeypatch):
    from reviewer.app import create_app
    from reviewer.models import ChangedFile
    from reviewer.settings import get_settings

    class FakeGerrit:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def changed_files(self, change_id: str):
            assert change_id == "Iabc123"
            return [ChangedFile(path="src/queue.py", lines_inserted=3, lines_deleted=1)]

        async def file_diff(self, change_id: str, file_path: str):
            assert (change_id, file_path) == ("Iabc123", "src/queue.py")
            return "--- a/src/queue.py\n+++ b/src/queue.py\n@@ -1 +1 @@\n-old\n+new\n"

    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setenv("GERRIT_AUTH", "Bearer test-auth")
    monkeypatch.setattr("reviewer.app.DB_PATH", Path(tempfile.mkdtemp()) / "diffs.db")
    monkeypatch.setattr("reviewer.app.GerritClient", FakeGerrit)
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        headers = {"X-Auth-Token": "test-token"}
        files = client.get("/gerrit/changes/Iabc123/files", headers=headers)
        assert files.status_code == 200
        assert files.json() == {"files": [{
            "path": "src/queue.py", "lines_inserted": 3, "lines_deleted": 1, "status": "M",
        }]}

        diff = client.get("/gerrit/changes/Iabc123/files/src/queue.py/diff", headers=headers)
        assert diff.status_code == 200
        assert diff.json() == {"path": "src/queue.py", "diff": "--- a/src/queue.py\n+++ b/src/queue.py\n@@ -1 +1 @@\n-old\n+new\n"}

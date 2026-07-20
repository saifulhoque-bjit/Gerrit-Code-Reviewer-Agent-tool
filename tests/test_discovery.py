"""Gerrit project and open-change discovery routes."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


def test_discovery_routes_return_safe_project_and_change_summaries(monkeypatch):
    from reviewer.app import create_app
    from reviewer.settings import get_settings

    class FakeGerrit:
        def __init__(self, *_args):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def list_projects(self):
            return ["apps/reviewer", "libs/shared"]

        async def open_changes(self, project: str = "", limit: int = 100):
            assert project == "apps/reviewer"
            assert limit == 25
            return [{
                "change_id": "Iabc123", "number": 101, "project": project,
                "subject": "Fix review queue", "branch": "main",
                "owner": "Saiful Hoque", "created": "2026-07-17 09:00:00.000000000",
                "updated": "2026-07-17 10:00:00.000000000", "status": "NEW",
            }]

    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setenv("GERRIT_AUTH", "Bearer test-auth")
    monkeypatch.setattr("reviewer.app.DB_PATH", Path(tempfile.mkdtemp()) / "discovery.db")
    monkeypatch.setattr("reviewer.app.GerritClient", FakeGerrit)
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        headers = {"X-Auth-Token": "test-token"}
        projects = client.get("/gerrit/projects", headers=headers)
        assert projects.status_code == 200
        assert projects.json() == {"projects": ["apps/reviewer", "libs/shared"]}

        changes = client.get(
            "/gerrit/changes/open?project=apps%2Freviewer&limit=25", headers=headers,
        )
        assert changes.status_code == 200
        assert changes.json()["changes"] == [{
            "change_id": "Iabc123", "number": 101, "project": "apps/reviewer",
            "subject": "Fix review queue", "branch": "main",
            "owner": "Saiful Hoque", "created": "2026-07-17 09:00:00.000000000",
            "updated": "2026-07-17 10:00:00.000000000", "status": "NEW",
        }]

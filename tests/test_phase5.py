"""Phase 5: direct worker, webhook, review history, and feedback."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ["API_TOKEN"] = "phase5-token"

from reviewer.db import connect, init_db
from reviewer.models import ReviewComment, TokenUsage
from reviewer.settings import get_settings
from reviewer.store import finish_review, start_review

HDR = {"X-Auth-Token": "phase5-token"}


def test_direct_worker_parses_openai_response_and_reports_usage(monkeypatch):
    from reviewer.worker_direct import review_file_direct

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": '[{"file":"a.py","line":4,"severity":"warning","comment":"x"}]'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 7},
            }

    class Client:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json):
            assert url == "https://model.example/v1/chat/completions"
            assert json["model"] == "test-model"
            return Response()

    monkeypatch.setattr("reviewer.worker_direct.httpx.AsyncClient", Client)
    comments, usage = asyncio_run(
        review_file_direct("a.py", "+x", [], "proj", 8000, 30,
                           base_url="https://model.example/v1/", model="test-model", api_key="key")
    )
    assert comments[0].line == 4
    assert usage.input_tokens == 12 and usage.output_tokens == 7


# pytest's sync tests stay portable without an asyncio plugin-specific decorator.
def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


def test_history_feedback_and_effectiveness_endpoints(monkeypatch):
    from reviewer.app import create_app
    from reviewer.settings import get_settings

    monkeypatch.setenv("API_TOKEN", "phase5-token")
    db_path = Path(tempfile.mkdtemp()) / "history.db"
    monkeypatch.setattr("reviewer.app.DB_PATH", db_path)
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        conn = client.app.state.conn
        review_id = start_review(conn, "100", 1, "proj", "main")
        finish_review(conn, review_id, [ReviewComment(file="a.py", line=2, severity="warning", comment="x")], TokenUsage(files_reviewed=1))
        comment_id = conn.execute("SELECT id FROM comment WHERE review_id=?", (review_id,)).fetchone()[0]

        history = client.get("/reviews", headers=HDR)
        assert history.status_code == 200
        assert history.json()["reviews"][0]["change_id"] == "100"

        saved = client.get("/review/100?patchset=1", headers=HDR)
        assert saved.status_code == 200
        assert saved.json()["patchset"] == 1
        assert saved.json()["comments"][0]["comment"] == "x"

        feedback = client.post(f"/comments/{comment_id}/feedback", headers=HDR, json={"feedback": "useful"})
        assert feedback.status_code == 200 and feedback.json()["feedback"] == "useful"
        effectiveness = client.get("/rules/effectiveness", headers=HDR)
        assert effectiveness.json()["effectiveness"] == [
            {"severity": "warning", "useful": 1, "dismissed": 0, "total": 1}
        ]


def test_webhook_requires_secret_and_accepts_patchset_created(monkeypatch):
    from reviewer.app import create_app
    from reviewer.settings import get_settings

    monkeypatch.setenv("WEBHOOK_SECRET", "hook-secret")
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        payload = {"type": "patchset-created", "change": {"id": "I123", "project": "proj"}}
        assert client.post("/hooks/gerrit", json=payload).status_code == 401
        response = client.post("/hooks/gerrit", headers={"X-Webhook-Secret": "hook-secret"}, json=payload)
        assert response.status_code == 202
        assert response.json()["change_id"] == "I123"

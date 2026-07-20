"""Credential login uses an HttpOnly local session, never browser-stored Basic auth."""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient


def test_login_validates_gerrit_and_authenticates_session(monkeypatch):
    from reviewer.app import create_app
    from reviewer.settings import get_settings

    class FakeGerrit:
        def __init__(self, url, auth, *_args):
            self.auth = auth

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            pass

        async def account_self(self):
            if self.auth.endswith("OmJhZA=="):
                import httpx
                request = httpx.Request("GET", "https://gerrit.example/a/accounts/self")
                raise httpx.HTTPStatusError("unauthorized", request=request, response=httpx.Response(401, request=request))
            return {"username": "saiful", "name": "Saiful Hoque"}

    monkeypatch.setenv("API_TOKEN", "test-token")
    monkeypatch.setattr("reviewer.app.DB_PATH", Path(tempfile.mkdtemp()) / "auth.db")
    monkeypatch.setattr("reviewer.app.GerritClient", FakeGerrit)
    get_settings.cache_clear()

    with TestClient(create_app()) as client:
        invalid = client.post("/auth/login", json={"username": "saiful", "http_password": "bad"})
        assert invalid.status_code == 401

        response = client.post("/auth/login", json={"username": "saiful", "http_password": "good"})
        assert response.status_code == 200
        assert response.json() == {"username": "saiful", "display_name": "Saiful Hoque"}
        assert "reviewer_session" in response.headers["set-cookie"]
        assert "HttpOnly" in response.headers["set-cookie"]
        assert "Basic " not in response.text

        assert client.get("/auth/status").json() == {"authenticated": True, "username": "saiful", "display_name": "Saiful Hoque"}
        assert client.get("/reviews").status_code == 200

        assert client.post("/auth/logout").status_code == 204
        assert client.get("/auth/status").json() == {"authenticated": False}
        assert client.get("/reviews").status_code == 401

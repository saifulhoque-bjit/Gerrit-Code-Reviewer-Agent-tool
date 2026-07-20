"""Phase 4: /review/{id}/post endpoint wiring — TestClient, no network."""
from __future__ import annotations

import os

os.environ["API_TOKEN"] = "test-secret-123"

from fastapi.testclient import TestClient

from reviewer.app import create_app

HDR = {"X-Auth-Token": "test-secret-123"}


def test_post_route_registered():
    app = create_app()
    paths = {r.path for r in app.routes}
    assert "/review/{change_id}/post" in paths


def test_post_requires_token():
    with TestClient(create_app()) as client:
        r = client.post("/review/12345/post")  # no token
        assert r.status_code == 401


def test_post_503_without_gerrit_auth(monkeypatch):
    # API_TOKEN is set, but no GERRIT_* → auth_header() empty → 503.
    monkeypatch.delenv("GERRIT_AUTH", raising=False)
    monkeypatch.delenv("GERRIT_USER", raising=False)
    monkeypatch.delenv("GERRIT_HTTP_PASSWORD", raising=False)
    from reviewer.settings import get_settings
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        r = client.post("/review/12345/post", headers=HDR)
        # 503 (no gerrit auth) proves the route + token gate both passed.
        assert r.status_code == 503


def test_post_selective_body_accepted(monkeypatch):
    # A comment_ids body still reaches the gerrit-auth gate (503), proving the
    # selective-post payload is parsed rather than rejected as malformed.
    monkeypatch.delenv("GERRIT_AUTH", raising=False)
    monkeypatch.delenv("GERRIT_USER", raising=False)
    monkeypatch.delenv("GERRIT_HTTP_PASSWORD", raising=False)
    from reviewer.settings import get_settings
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        r = client.post("/review/12345/post", headers=HDR, json={"comment_ids": [1, 2]})
        assert r.status_code == 503


def test_edit_comment_route_registered():
    app = create_app()
    assert "/comments/{comment_id}" in {r.path for r in app.routes}


def test_edit_comment_requires_token():
    with TestClient(create_app()) as client:
        r = client.patch("/comments/1", json={"comment": "x"})
        assert r.status_code == 401


def test_edit_comment_404_when_missing():
    with TestClient(create_app()) as client:
        r = client.patch("/comments/999999", headers=HDR, json={"comment": "new text"})
        assert r.status_code == 404


def test_edit_comment_422_on_empty():
    with TestClient(create_app()) as client:
        r = client.patch("/comments/1", headers=HDR, json={"comment": "   "})
        assert r.status_code == 422

"""Smoke tests for the FastAPI app — health open, config token-gated."""
import os

os.environ.setdefault("API_TOKEN", "test-token")

from fastapi.testclient import TestClient

from reviewer.app import create_app

client = TestClient(create_app())


def test_health_is_open():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_config_requires_token():
    assert client.get("/config").status_code == 401
    assert client.get("/config", headers={"X-Auth-Token": "wrong"}).status_code == 401


def test_config_with_token():
    r = client.get("/config", headers={"X-Auth-Token": "test-token"})
    assert r.status_code == 200
    assert "gerrit" in r.json()
    # secrets must never appear in /config
    assert "secrets" not in r.json()

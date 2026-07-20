"""Phase 5: rule browsing + editing API and the pure rules helpers.

Points the app at a temp rules tree via REVIEWER_CONFIG so nothing touches the
repo's real rules/ directory. Path-traversal guards are the security-critical
part — a bad `path=` must never read or write outside rules_dir.
"""
from __future__ import annotations

import os

os.environ["API_TOKEN"] = "test-secret-123"

import pytest
from fastapi.testclient import TestClient

from reviewer.rules import (
    list_rule_files,
    read_rule_file,
    write_rule_file,
    _safe_rule_file,
)

HDR = {"X-Auth-Token": "test-secret-123"}


# ── pure helpers ──────────────────────────────────────────────────
def _seed_tree(root):
    (root / "_base").mkdir(parents=True)
    (root / "_base" / "default.md").write_text("base rules", encoding="utf-8")
    (root / "_lang").mkdir()
    (root / "_lang" / "python.md").write_text("py rules", encoding="utf-8")


def test_list_groups_by_tier(tmp_path):
    _seed_tree(tmp_path)
    files = list_rule_files(tmp_path)
    by_path = {f["path"]: f for f in files}
    assert set(by_path) == {"_base/default.md", "_lang/python.md"}
    assert by_path["_base/default.md"]["tier"] == "_base"
    assert by_path["_lang/python.md"]["bytes"] == len("py rules")


def test_list_missing_dir_is_empty(tmp_path):
    assert list_rule_files(tmp_path / "nope") == []


def test_read_and_write_roundtrip(tmp_path):
    _seed_tree(tmp_path)
    assert read_rule_file(tmp_path, "_base/default.md") == "base rules"
    meta = write_rule_file(tmp_path, "_base/default.md", "edited")
    assert meta["path"] == "_base/default.md" and meta["bytes"] == len("edited")
    assert read_rule_file(tmp_path, "_base/default.md") == "edited"


def test_write_creates_new_file_and_tier(tmp_path):
    _seed_tree(tmp_path)
    write_rule_file(tmp_path, "myproj/conventions.md", "team rules")
    assert (tmp_path / "myproj" / "conventions.md").read_text(encoding="utf-8") == "team rules"


@pytest.mark.parametrize("bad", [
    "../escape.md", "../../etc/passwd.md", "/abs/path.md",
    "_base/../../escape.md", "notes.txt", "", "  ", "_base/x.py",
])
def test_traversal_and_suffix_guards(tmp_path, bad):
    _seed_tree(tmp_path)
    with pytest.raises(ValueError):
        _safe_rule_file(tmp_path, bad)


# ── API endpoints ─────────────────────────────────────────────────
@pytest.fixture()
def app_with_rules(tmp_path, monkeypatch):
    _seed_tree(tmp_path / "rules")
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[review]\n"
        f'rules_dir = "{(tmp_path / "rules").as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REVIEWER_CONFIG", str(cfg))
    import reviewer.settings as s
    s.get_settings.cache_clear()
    monkeypatch.setattr(s, "CONFIG_PATH", cfg)
    from reviewer.app import create_app
    with TestClient(create_app()) as client:
        yield client
    s.get_settings.cache_clear()


def test_rules_endpoints_require_token(app_with_rules):
    assert app_with_rules.get("/rules").status_code == 401
    assert app_with_rules.get("/rules/file?path=_base/default.md").status_code == 401
    assert app_with_rules.put("/rules/file?path=_base/default.md",
                              json={"content": "x"}).status_code == 401


def test_list_rules_endpoint(app_with_rules):
    r = app_with_rules.get("/rules", headers=HDR)
    assert r.status_code == 200
    paths = {f["path"] for f in r.json()["files"]}
    assert paths == {"_base/default.md", "_lang/python.md"}


def test_read_rule_endpoint(app_with_rules):
    r = app_with_rules.get("/rules/file", headers=HDR, params={"path": "_lang/python.md"})
    assert r.status_code == 200 and r.json()["content"] == "py rules"


def test_read_missing_is_404(app_with_rules):
    r = app_with_rules.get("/rules/file", headers=HDR, params={"path": "_base/ghost.md"})
    assert r.status_code == 404


def test_read_traversal_is_400(app_with_rules):
    r = app_with_rules.get("/rules/file", headers=HDR, params={"path": "../../secrets.md"})
    assert r.status_code == 400


def test_write_rule_endpoint_persists(app_with_rules):
    r = app_with_rules.put("/rules/file", headers=HDR,
                           params={"path": "_base/default.md"},
                           json={"content": "new base body"})
    assert r.status_code == 200 and r.json()["bytes"] == len("new base body")
    back = app_with_rules.get("/rules/file", headers=HDR, params={"path": "_base/default.md"})
    assert back.json()["content"] == "new base body"


def test_write_traversal_is_400(app_with_rules):
    r = app_with_rules.put("/rules/file", headers=HDR,
                           params={"path": "../evil.md"}, json={"content": "x"})
    assert r.status_code == 400

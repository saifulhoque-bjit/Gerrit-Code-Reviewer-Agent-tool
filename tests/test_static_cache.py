"""Browser-facing static responses must not serve stale dashboard HTML."""
from fastapi.testclient import TestClient


def test_dashboard_html_disables_browser_caching():
    from reviewer.app import create_app

    with TestClient(create_app()) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_inspect_opens_the_workspace_inline():
    from reviewer.app import STATIC_DIR

    dashboard = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # Inspect opens the in-page workspace directly; a popup window gets blocked
    # or auto-closed by the browser, so it must not spawn one.
    assert "openWorkspace(changeId)" in dashboard
    assert "window.open(" not in dashboard
    # Direct deep-links (/?change=123) still bootstrap the workspace on load.
    assert 'new URLSearchParams(window.location.search).get("change")' in dashboard

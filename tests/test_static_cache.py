"""Browser-facing static responses must not serve stale dashboard HTML."""
from fastapi.testclient import TestClient


def test_dashboard_html_disables_browser_caching():
    from reviewer.app import create_app

    with TestClient(create_app()) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_inspect_opens_a_change_specific_new_tab():
    from reviewer.app import STATIC_DIR

    dashboard = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # Inspect opens a real new tab. A features string (3rd arg) makes it a popup
    # the browser blocks/auto-closes, so window.open must use only "_blank".
    assert 'window.open(`/?change=${encodeURIComponent(changeId)}`, "_blank")' in dashboard
    assert '"_blank", "noopener"' not in dashboard
    # The new tab bootstraps the workspace from the ?change= param on load.
    assert 'new URLSearchParams(window.location.search).get("change")' in dashboard

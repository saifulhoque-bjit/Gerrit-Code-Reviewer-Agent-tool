"""Browser-facing static responses must not serve stale dashboard HTML."""
from fastapi.testclient import TestClient


def test_dashboard_html_disables_browser_caching():
    from reviewer.app import create_app

    with TestClient(create_app()) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-cache, no-store, must-revalidate"


def test_inspect_opens_a_dedicated_change_page():
    from reviewer.app import STATIC_DIR

    dashboard = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # Like v3's openChange: a plain same-tab navigation, not a scripted
    # window.open (which gets popup-blocked / auto-closed).
    assert "window.location.href = `/?change=${encodeURIComponent(changeId)}`" in dashboard
    assert "window.open(" not in dashboard
    # ?change= reloads into a dedicated full-page view (v3's review.html feel):
    # the queue + controls hide, only the workspace shows.
    assert 'new URLSearchParams(window.location.search).get("change")' in dashboard
    assert 'document.body.classList.add("change-view")' in dashboard
    assert "body.change-view #queuePanel { display: none; }" in dashboard

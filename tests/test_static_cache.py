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


def test_diff_has_unified_and_side_by_side_views():
    from reviewer.app import STATIC_DIR

    dashboard = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # A Unified | Side-by-side toggle, like v3's setView('unified'|'split').
    assert 'data-view="unified"' in dashboard
    assert 'data-view="split"' in dashboard
    # Both renderers exist and the dispatcher honors the selected mode.
    assert "function renderUnified(diff)" in dashboard
    assert "function renderSplit(diff)" in dashboard
    assert 'diffMode === "split" ? renderSplit(diff) : renderUnified(diff)' in dashboard


def test_dashboard_has_select_edit_post_workflow():
    from reviewer.app import STATIC_DIR

    dashboard = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # Select / deselect / post controls (v3's Select All + Post to Gerrit).
    assert 'id="selectAll"' in dashboard and 'id="deselectAll"' in dashboard
    assert 'id="postSelected"' in dashboard and 'id="postBar"' in dashboard
    # Per-comment checkbox + inline edit affordances.
    assert 'data-pick=' in dashboard and 'data-edit=' in dashboard
    # Posts only the selected ids, and edits persist over PATCH.
    assert "comment_ids: ids" in dashboard
    assert 'method: "PATCH"' in dashboard


def test_dashboard_has_rules_and_analytics_panels():
    from reviewer.app import STATIC_DIR

    dashboard = (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    # Nav entry points + panels.
    assert 'id="rulesBtn"' in dashboard and 'id="analyticsBtn"' in dashboard
    assert 'id="rulesPanel"' in dashboard and 'id="analyticsPanel"' in dashboard
    # Rules browse/read/save wiring.
    assert 'fetch("/rules")' in dashboard
    assert "/rules/file?path=" in dashboard
    assert 'method: "PUT"' in dashboard
    # Analytics reads the effectiveness endpoint.
    assert 'fetch("/rules/effectiveness")' in dashboard

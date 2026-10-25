"""Production V3 workspace must retain the approved command-center shell."""
from pathlib import Path
from jinja2 import Environment

ROOT=Path(__file__).resolve().parents[1]
def test_production_v3_shell_and_import_audit():
    content=(ROOT/"templates/v3/workspace.html").read_text()
    for token in ('class="shell design2-shell"',
                  "{% include 'v3/_sidebar.html' %}",
                  "{% include 'v3/_topbar.html' %}",
                  "Imported WO audit"):
        assert token in content
    assert "is_dev_mode" not in content
    Environment().parse(content)
def test_ordinary_login_without_developer_mode():
    routes=(ROOT/"v3_routes.py").read_text()
    main=(ROOT/"main.py").read_text()
    assert 'T8972' not in routes
    assert '/dev/workspace' not in routes
    assert '/dev/login' not in main
    assert 'dev_mode_sandbox' not in main
    assert 'IND3_OLLAMA_MODEL' in (ROOT/"v3_ollama_background.py").read_text()


def test_workshop_sidebar_restored_without_test_clock():
    sidebar = (ROOT/"templates/v3/_sidebar.html").read_text()
    css = (ROOT/"static/v3_design2.css").read_text()
    assert 'id="v3-sidebar"' in sidebar
    # Full V3 page navigation preserves active sidebar states and lets the
    # recovered Analytics Chart.js and Administration scripts initialize.
    # Main-only HTMX swaps discarded the scripts on the recovered pages.
    assert 'href="/v3/history?shift_id={{ sid }}"' in sidebar
    assert 'href="/v3/analytics?shift_id={{ sid }}"' in sidebar
    assert 'href="/v3/admin"' in sidebar
    assert 'hx-select="#main-content"' not in sidebar
    assert 'class="d2-sidebar-profile"' in sidebar
    assert 'href="/v3/logout"' in sidebar
    assert 'data-v3-live-time' in sidebar
    assert 'data-v3-live-date' in sidebar
    assert sidebar.index('data-v3-live-date') < sidebar.index('<nav class="nav-block">')
    assert 'data-v3-live-clock' not in (ROOT/"templates/v3/_topbar.html").read_text()
    script = (ROOT/"static/v3_live_clock.js").read_text()
    assert 'America/Indiana/Indianapolis' in script
    assert 'document.querySelectorAll("[data-v3-live-time]")' in script
    assert "sidebar_clock_state" not in sidebar
    assert "TEST MODE" not in sidebar
    assert ".navy-sidebar .d2-sidebar-profile" in css
    assert ".navy-sidebar .d2-signout" in css
    Environment().parse(sidebar)

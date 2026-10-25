"""V3 test mode uses the live server clock, not a frozen mock date."""
from datetime import datetime
from pathlib import Path
import dev_mode
ROOT=Path(__file__).resolve().parents[1]

def test_live_dev_clock():
    before=datetime.now()
    now=dev_mode.dev_now()
    after=datetime.now()
    assert before <= now <= after
    with dev_mode.freeze_time(now):
        assert dev_mode.is_active()
        assert dev_mode.get_today() == now.date()
    assert not dev_mode.is_active()

def test_test_clock_ui_removed():
    html=(ROOT/"templates/v3/dev_workspace.html").read_text()
    workspace=(ROOT/"templates/v3/workspace.html").read_text()
    main=(ROOT/"main.py").read_text()
    routes=(ROOT/"v3_routes.py").read_text()
    assert 'data-v3-live-clock' in html
    assert 'name="simulated_at"' not in html
    assert 'SANDBOX DATE &amp; TIME' not in html
    assert 'test_clock_frozen' not in workspace
    assert 'dev_mode.dev_now()' in main
    assert 'dev_now(request.cookies' not in main
    assert 'set_cookie("dev_clock"' not in routes
    assert 'response.delete_cookie("dev_clock"' in routes

def test_real_clock_display_kept():
    topbar=(ROOT/"templates/v3/_topbar.html").read_text()
    js=(ROOT/"static/v3_live_clock.js").read_text()
    assert 'data-v3-live-clock' in topbar
    assert "America/Indiana/Indianapolis" in js

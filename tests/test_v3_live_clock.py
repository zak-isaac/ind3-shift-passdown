"""Clock and sandbox-screen regressions for the IND3 V3 test workspace."""
from datetime import datetime
from pathlib import Path

import dev_mode

ROOT = Path(__file__).resolve().parents[1]


def test_simulated_date_is_independent_of_real_time():
    selected = dev_mode.dev_now("2026-10-24T05:45")
    assert selected == datetime(2026, 10, 24, 5, 45)
    with dev_mode.freeze_time(selected):
        assert dev_mode.is_active()
        assert dev_mode.get_today().isoformat() == "2026-10-24"
        assert dev_mode.get_now() == selected
    assert not dev_mode.is_active()


def test_default_sandbox_clock_follows_actual_calendar():
    before = datetime.now()
    value = dev_mode.dev_now()
    after = datetime.now()
    assert before <= value <= after
    assert before <= dev_mode.dev_now("invalid test time") <= datetime.now()


def test_live_clock_visible_and_test_clock_explicit():
    dev_page = (ROOT / "templates/v3/dev_workspace.html").read_text()
    workspace = (ROOT / "templates/v3/workspace.html").read_text()
    topbar = (ROOT / "templates/v3/_topbar.html").read_text()
    script = (ROOT / "static/v3_live_clock.js").read_text()
    assert "LIVE DATE &amp; TIME" in dev_page
    assert "data-v3-live-clock" in dev_page
    assert "clock_is_frozen" in dev_page
    assert "test_clock_label" in workspace
    assert "data-v3-live-clock" in topbar
    assert "America/Indiana/Indianapolis" in script
    assert "setInterval(renderClock, 1000)" in script

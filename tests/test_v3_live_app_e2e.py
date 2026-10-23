"""Characterization test for the shared V2/V3 plumbing, driven through the
REAL wired `main.app` -- not the isolated create_v3_router() test harness
used by tests/test_v3_*.py.

Why this exists: wiring V3 into main.py (see main.py's "V3 shift workspace"
section) made V3 directly reuse real V2 business logic (lead_generate, the
roster, the auth/eligibility helpers). The isolated v3_routes.py unit tests
inject *fake* versions of all of that, so they can't catch a regression in
the real thing. This test is the safety net for the next phase of the V2 UI
cutover: before deleting the big V2 dashboards (lead.html/tech.html and the
~80 routes around them), this test must keep passing, because it proves the
plumbing V3 actually depends on survives the cut.

Fully isolated from production: routes every request through main.py's own
dev-mode sandbox (same mechanism a human does via /dev/login), pointed at a
throwaway per-test SQLite file instead of the real dbs/_devtest.db, with a
fully-pinned fake "now" so it never depends on the real wall-clock time of
day for shift-window validity.
"""
import re
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import db
import dev_mode
import main
import registry


def _extract_error(html):
    match = re.search(r'class="error"[^>]*>(.*?)</div>', html, re.S)
    return match.group(1).strip() if match else html[:500]

TEST_ASSOC = "9801"
TEST_PROFILE = {
    "display_name": "QA Associate",
    "full_name": "QA Associate",
    "assoc_num": TEST_ASSOC,
    "default_id": "qa",
    "shift": "S5",
    "status": "MST",
    "is_manager": False,
    "is_mst": True,
}
# Friday evening -- squarely inside S5's window (17:30 start) -- fully fixed
# so this test never depends on the real wall-clock time of day.
FIXED_NOW = datetime(2026, 8, 7, 20, 0, 0)


@pytest.fixture
def live_client(monkeypatch, tmp_path):
    """TestClient over the REAL app, sandboxed to a throwaway DB file and a
    pinned fake clock -- see module docstring.

    Critically: entering TestClient(main.app) runs the REAL lifespan, which
    calls db.set_db_path(registry.db_path_for(active_slug)) and then purges
    orphaned shifts/OT codes against whatever that resolves to. Without the
    db_path_for patch below, that would hit the real dbs/ind3.db directly --
    the dev-mode sandbox cookie only redirects *request-scoped* db calls, it
    has no effect on startup-time code."""
    monkeypatch.setitem(main._ROSTER, TEST_ASSOC, TEST_PROFILE)
    monkeypatch.setattr(registry, "db_path_for", lambda slug: tmp_path / "live_e2e_facility.db")
    monkeypatch.setattr(dev_mode, "DEV_DB_PATH", tmp_path / "live_e2e_dev.db")
    monkeypatch.setattr(dev_mode, "dev_now", lambda: FIXED_NOW)

    with db.use_sandbox_db(dev_mode.DEV_DB_PATH):
        db.init_db()

    with TestClient(main.app) as client:
        resp = client.post("/dev/login", data={"code": dev_mode.DEV_CODE}, follow_redirects=False)
        assert resp.status_code == 303
        assert client.cookies.get(dev_mode.DEV_COOKIE)
        yield client


def _start_shift_via_real_login(client):
    """Drive the real /v3/login POST (same route a tech actually uses) so
    the server sets its own signed session cookie -- exercises the real
    eligibility/window logic too, instead of hand-rolling a cookie."""
    resp = client.post(
        "/v3/login",
        data={"associate_id": TEST_ASSOC, "mode": "start"},
        follow_redirects=False,
    )
    assert resp.status_code == 303, _extract_error(resp.text)
    shift_id = int(resp.headers["location"].split("shift_id=")[1])
    return shift_id


def test_full_shift_lifecycle_through_real_app(live_client):
    """Login session -> manual entry -> generate report (real lead_generate)
    -> every export format (shared /report/* routes V3's own report.html
    links to) -> end shift, all through the REAL main.app wiring."""
    shift_id = _start_shift_via_real_login(live_client)

    workspace = live_client.get(f"/v3?shift_id={shift_id}", follow_redirects=False)
    assert workspace.status_code == 200, workspace.headers.get("location")

    manual = live_client.post(
        "/v3/manual",
        data={"shift_id": shift_id, "unit": "PUMP0099", "issue": "QA characterization test entry"},
        follow_redirects=False,
    )
    assert manual.status_code == 303

    generated = live_client.post("/v3/generate", data={"shift_id": shift_id}, follow_redirects=False)
    assert generated.status_code == 303
    report_id = int(re.search(r"/v3/report/(\d+)", generated.headers["location"]).group(1))

    report_page = live_client.get(f"/v3/report/{report_id}?shift_id={shift_id}")
    assert report_page.status_code == 200
    assert "PUMP0099" in report_page.text

    # Shared download infrastructure V3's own report.html links to directly
    # (NOT /v3/-prefixed) -- these must survive any future V2 cutover.
    pdf = live_client.get(f"/report/{report_id}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"

    docx = live_client.get(f"/report/{report_id}/docx")
    assert docx.status_code == 200

    text = live_client.get(f"/report/{report_id}/download")
    assert text.status_code == 200
    assert "PUMP0099" in text.text

    with db.use_sandbox_db(dev_mode.DEV_DB_PATH):
        assert db.get_shift(shift_id)["is_active"]

    ended = live_client.post("/v3/end", data={"shift_id": shift_id}, follow_redirects=False)
    assert ended.status_code == 303

    with db.use_sandbox_db(dev_mode.DEV_DB_PATH):
        assert not db.get_shift(shift_id)["is_active"]

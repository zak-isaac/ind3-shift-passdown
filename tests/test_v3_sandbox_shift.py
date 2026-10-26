"""Durable test/sandbox shift (2026-10-24 request): Isaac needs a persistent
shift he can always reach for manual click-through testing, without it being
auto-closed by the real-shift time-window logic, without being deleted by
purge_orphaned_shifts() at server startup, and -- critically -- without ever
blocking or counting against a REAL technician/MST starting their own S1-S5
shift of the same type.

Root cause of the original failure: a sandbox shift created with a normal
shift_type (e.g. S1) is indistinguishable from a real shift to every
lifecycle check in the app. It got closed within moments (S1 wasn't
scheduled for that day) and then deleted outright on the next server
restart (purge_orphaned_shifts() treats any empty, inactive shift as
abandoned). The fix is a dedicated is_test_shift flag threaded through
every one of those checks.
"""
from datetime import datetime, date, time, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import db
from v3_routes import create_v3_router

ROOT = Path(__file__).resolve().parents[1]

ROSTER = {
    "1001": {"display_name": "Real MST", "is_mst": True, "is_manager": False, "shift": "S4"},
    "2759": {"display_name": "Isaac S", "is_mst": False, "is_manager": True, "shift": "S2"},
}


def _app(tmp_path, now):
    db.set_db_path(tmp_path / "sandbox.db")
    db.init_db()
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    app.include_router(create_v3_router(
        templates=Jinja2Templates(directory=str(ROOT / "templates")),
        is_lead_authed=lambda request: False,
        can_manage_shift=lambda request, sid: False,
        resolve_my_shift=lambda request: None,
        shift_window=lambda shift, d: (datetime.combine(d, time(5, 30)), datetime.combine(d, time(18, 0))),
        lead_redirect=lambda destination: None,
        get_mst_roster=lambda: ROSTER,
        start_eligibility=lambda person, shift, now: (person.get("shift") == shift, False, "Wrong assignment"),
        allowed_shift_days={"S4": set(range(7))},
        now_func=lambda: now,
        get_v2_cookie_value=lambda: "v2-lead-auth",
    ))
    return TestClient(app)


def test_test_shift_does_not_count_toward_active_cap(tmp_path):
    db.set_db_path(tmp_path / "cap.db")
    db.init_db()
    db.start_shift("2026-10-24", "S4", "Isaac's Workspace Test mode", opened_by_assoc="2759", is_test_shift=True)
    # Two REAL shifts must still be startable even with a test shift active.
    db.start_shift("2026-10-24", "S1", "Real MST 1")
    db.start_shift("2026-10-24", "S2", "Real MST 2")
    # A third REAL shift still correctly hits the cap.
    try:
        db.start_shift("2026-10-24", "S3", "Real MST 3")
        assert False, "expected max_active ValueError"
    except ValueError as exc:
        assert str(exc) == "max_active"


def test_purge_never_deletes_an_empty_inactive_test_shift(tmp_path):
    db.set_db_path(tmp_path / "purge.db")
    db.init_db()
    sid = db.start_shift("2026-10-24", "S4", "Isaac's Workspace Test mode",
                          opened_by_assoc="2759", is_test_shift=True)
    db.close_shift(sid)  # inactive AND zero entries/reports -- the exact
                         # shape purge_orphaned_shifts() normally deletes.
    purged = db.purge_orphaned_shifts()
    assert purged == 0
    assert db.get_shift(sid) is not None


def test_real_associate_can_start_own_shift_despite_same_type_test_shift(tmp_path):
    """The critical collision-safety fix: a real S4 MST must be able to
    start their own S4 shift even while Isaac's S4-typed sandbox is active."""
    now = datetime(2026, 10, 24, 9, 0)
    client = _app(tmp_path, now)
    db.start_shift("2026-10-24", "S4", "Isaac's Workspace Test mode",
                   opened_by_assoc="2759", is_test_shift=True)
    resp = client.post("/v3/login", data={
        "associate_id": "1001", "mode": "start", "shift_type": "S4", "shift_date": "2026-10-24",
    }, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    real_shifts = [s for s in db.get_active_shifts() if not s["is_test_shift"]]
    assert len(real_shifts) == 1
    assert real_shifts[0]["opened_by_assoc"] == "1001"


def test_master_select_resolves_real_shift_not_test_shift_of_same_type(tmp_path):
    now = datetime(2026, 10, 24, 9, 0)
    client = _app(tmp_path, now)
    test_sid = db.start_shift("2026-10-24", "S4", "Isaac's Workspace Test mode",
                              opened_by_assoc="2759", is_test_shift=True)
    verify = client.post("/v3/master/verify", data={"master_code": "WRONG"})
    assert "Invalid testing master code" in verify.text  # sanity: route exists
    # Directly exercise master_select via a manager-authenticated session.
    master_login = client.post("/v3/login", data={
        "associate_id": "2759", "mode": "start", "shift_type": "", "shift_date": "",
    })
    assert "master-shift-dialog" in master_login.text
    picked = client.post("/v3/master/select", data={"shift_type": "S4"}, follow_redirects=False)
    assert picked.status_code == 303
    new_sid = int(picked.headers["location"].split("shift_id=")[1])
    assert new_sid != test_sid, "master select must create/resolve the REAL S4 shift, never the sandbox"


def test_test_shift_never_auto_closed_regardless_of_time_of_day(tmp_path):
    """A real S4 shift started this morning WOULD be auto-closed by now
    (we're simulating 11pm, long past S4's 6pm+1hr grace cutoff) -- but the
    test shift must remain active no matter what 'now' is."""
    late_now = datetime(2026, 10, 24, 23, 0)
    client = _app(tmp_path, late_now)
    sid = db.start_shift("2026-10-24", "S4", "Isaac's Workspace Test mode",
                         opened_by_assoc="2759", is_test_shift=True)
    resp = client.get("/v3/login")
    assert resp.status_code == 200
    assert db.get_shift(sid)["is_active"] == 1


def test_login_page_shows_resume_link_for_test_shift(tmp_path):
    now = datetime(2026, 10, 24, 9, 0)
    client = _app(tmp_path, now)
    sid = db.start_shift("2026-10-24", "S4", "Isaac's Workspace Test mode",
                         opened_by_assoc="2759", is_test_shift=True)
    resp = client.get("/v3/login")
    assert resp.status_code == 200
    assert "Workspace Test mode" in resp.text  # Jinja autoescapes the apostrophe to &#39;
    assert f"/v3/attach/{sid}" in resp.text


def test_manager_can_attach_directly_to_test_shift(tmp_path):
    now = datetime(2026, 10, 24, 9, 0)
    client = _app(tmp_path, now)
    sid = db.start_shift("2026-10-24", "S4", "Isaac's Workspace Test mode",
                         opened_by_assoc="2759", is_test_shift=True)
    client.post("/v3/login", data={"associate_id": "2759", "mode": "start", "shift_type": "", "shift_date": ""})
    resp = client.post(f"/v3/attach/{sid}", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/v3?shift_id={sid}"

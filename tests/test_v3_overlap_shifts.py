"""Verifies the V3 login/start flow actually allows two overlapping shifts
(e.g. S4/S5 handover) to run at once, while still blocking a third concurrent
shift and blocking a duplicate of an already-active shift code. Drives the
real /v3/login POST handler end-to-end rather than calling db.start_shift()
directly, so this exercises the same eligibility/overlap logic MSTs hit live.
"""
from datetime import datetime, time, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import db
from v3_routes import create_v3_router

ROOT = Path(__file__).resolve().parents[1]


def _make_app(tmp_path, now):
    db.set_db_path(tmp_path / "overlap.db")
    db.init_db()
    roster = {
        "1001": {"display_name": "First Starter", "shift": "S1", "is_mst": True, "is_manager": False},
        "2002": {"display_name": "Second Starter", "shift": "S2", "is_mst": True, "is_manager": False},
        "3003": {"display_name": "Third Starter", "shift": "S3", "is_mst": True, "is_manager": False},
    }
    app = FastAPI()
    app.include_router(create_v3_router(
        Jinja2Templates(directory=str(ROOT / "templates")),
        lambda request: False, lambda request, shift: False, lambda request: None,
        lambda shift, d: (datetime.combine(d, time(0)), datetime.combine(d + timedelta(days=1), time(0))),
        lambda url: RedirectResponse(url, status_code=303),
        get_mst_roster=lambda: roster,
        now_func=lambda: now,
    ))
    return TestClient(app)


def test_two_distinct_shifts_can_overlap_but_a_third_is_blocked(tmp_path):
    client = _make_app(tmp_path, datetime(2026, 10, 20, 12, 0))

    first = client.post("/v3/login", data={"associate_id": "1001"}, follow_redirects=False)
    assert first.status_code == 303, first.text[:300]

    second = client.post("/v3/login", data={"associate_id": "2002"}, follow_redirects=False)
    assert second.status_code == 303, second.text[:300]
    assert len(db.get_active_shifts()) == 2

    third = client.post("/v3/login", data={"associate_id": "3003"}, follow_redirects=False)
    assert third.status_code == 200
    assert "Two overlapping shifts are already running" in third.text
    assert len(db.get_active_shifts()) == 2


def test_duplicate_shift_code_blocked_even_with_room_to_spare(tmp_path):
    # A second associate independently assigned to the SAME shift code (S1)
    # must never be allowed to open a duplicate instance of that shift, even
    # though only one shift is running and there is room under the two-shift cap.
    db.set_db_path(tmp_path / "overlap_dup.db")
    db.init_db()
    roster = {
        "1001": {"display_name": "First S1 Starter", "shift": "S1", "is_mst": True, "is_manager": False},
        "1002": {"display_name": "Second S1 Starter", "shift": "S1", "is_mst": True, "is_manager": False},
    }
    app = FastAPI()
    app.include_router(create_v3_router(
        Jinja2Templates(directory=str(ROOT / "templates")),
        lambda request: False, lambda request, shift: False, lambda request: None,
        lambda shift, d: (datetime.combine(d, time(0)), datetime.combine(d + timedelta(days=1), time(0))),
        lambda url: RedirectResponse(url, status_code=303),
        get_mst_roster=lambda: roster,
        now_func=lambda: datetime(2026, 10, 20, 12, 0),
    ))
    client = TestClient(app)

    first = client.post("/v3/login", data={"associate_id": "1001"}, follow_redirects=False)
    assert first.status_code == 303

    dup = client.post("/v3/login", data={"associate_id": "1002"}, follow_redirects=False)
    assert dup.status_code == 200
    assert "is already active" in dup.text
    assert len(db.get_active_shifts()) == 1

    resume = client.post("/v3/login", data={"associate_id": "1001"}, follow_redirects=False)
    assert resume.status_code == 303
    assert len(db.get_active_shifts()) == 1


def test_shift_stays_active_one_hour_past_scheduled_end_then_closes(tmp_path):
    """A shift must remain active from 1h before its scheduled start through
    1h after its scheduled end -- a symmetric handover grace period, not just
    a head start. Window here: 06:00-14:00."""
    db.set_db_path(tmp_path / "grace_period.db")
    db.init_db()
    roster = {"1001": {"display_name": "Grace Period Tester", "shift": "S1", "is_mst": True, "is_manager": False}}
    now_box = {"t": datetime(2026, 10, 20, 7, 0)}

    def shift_window(shift_type, d):
        return (datetime.combine(d, time(6, 0)), datetime.combine(d, time(14, 0)))

    app = FastAPI()
    app.include_router(create_v3_router(
        Jinja2Templates(directory=str(ROOT / "templates")),
        lambda request: False, lambda request, shift: False, lambda request: None,
        shift_window,
        lambda url: RedirectResponse(url, status_code=303),
        get_mst_roster=lambda: roster,
        now_func=lambda: now_box["t"],
    ))
    client = TestClient(app)

    started = client.post("/v3/login", data={"associate_id": "1001"}, follow_redirects=False)
    assert started.status_code == 303, started.text[:300]
    sid = int(started.headers["location"].split("shift_id=")[1])

    # 30 minutes after the 14:00 scheduled end -- still inside the 1h grace window.
    now_box["t"] = datetime(2026, 10, 20, 14, 30)
    still_active = client.get("/v3", params={"shift_id": sid})
    assert still_active.status_code == 200
    assert db.get_shift(sid)["is_active"] == 1

    # 90 minutes after the scheduled end -- past the 1h grace window, must close.
    now_box["t"] = datetime(2026, 10, 20, 15, 30)
    client.get("/v3", params={"shift_id": sid}, follow_redirects=False)
    assert db.get_shift(sid)["is_active"] == 0




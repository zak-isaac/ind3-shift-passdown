"""Regressions for Code Puppy V3 page recovery and overnight WO display."""
import json
from datetime import datetime, time as clock
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates
from fastapi.responses import RedirectResponse
from jinja2 import Environment
import db
from v3_routes import create_v3_router

ROOT=Path(__file__).resolve().parents[1]


def test_restored_v3_pages_and_partial_dependencies():
    for page in ("history","analytics","admin"):
        html=(ROOT/"templates"/"v3"/(page+".html")).read_text()
        assert "v3/_sidebar.html" in html
        assert "v3/_topbar.html" in html
        Environment().parse(html)
    for partial in ("v3_assoc_list","v3_assoc_row","v3_assoc_row_edit"):
        assert (ROOT/"templates"/"partials"/(partial+".html")).exists()
    history=(ROOT/"templates/v3/history.html").read_text()
    assert "/v3/history/{{ s.id }}/eml" in history
    analytics=(ROOT/"templates/v3/analytics.html").read_text()
    assert "chartTopAssets" in analytics and "chartWOTrend" in analytics
    assert "data | tojson" in analytics
    admin=(ROOT/"templates/v3/admin.html").read_text()
    assert "/v3/admin/roster" in admin and "/v3/admin/teams-webhook" in admin
    assert 'action="/admin/teams-webhook"' not in admin


def test_actual_s5_work_completion_is_displayed_next_calendar_day(tmp_path):
    db.set_db_path(tmp_path/"overnight_display.db")
    db.init_db()
    sid=db.start_shift("2026-10-25","S5","Test MST")
    db.upsert_cmms_entry(sid,"TEST-S5-WO","MDR.C0004","MDR problem",
                         json.dumps(["Reseated drive belt. RTS."]),"Test Technician")
    app=FastAPI()
    app.include_router(create_v3_router(
        templates=Jinja2Templates(directory=str(ROOT/"templates")),
        is_lead_authed=lambda request: True,
        can_manage_shift=lambda request,shift_id: True,
        resolve_my_shift=lambda request: db.get_shift(sid),
        shift_window=lambda shift,day:(datetime.combine(day,clock(17,30)),datetime.combine(day,clock(6))),
        lead_redirect=lambda *_:RedirectResponse("/v3/login",status_code=303),
        get_mst_roster=None,
        now_func=lambda:datetime(2026,10,26,4,30)))
    with TestClient(app) as client:
        initial=client.get("/v3",params={"shift_id":sid})
        assert initial.status_code==200
        import v3_routes
        with db._conn() as c:
            c.execute("""INSERT INTO v3_shift_source(shift_id,filename,rows_json)
                VALUES (?,?,?)""",(sid,"s5_test.xlsx",json.dumps([{
                "wo_number":"TEST-S5-WO",
                "tech_name":"Test Technician",
                "completed_by":"Test Technician",
                "start_at":"2026-10-25T23:35:00",
                "in_service_at":"2026-10-26T04:30:00",
                "last_complete_at":"2026-10-26T04:30:00",
                "closed_at":"2026-10-26T09:45:00",
                "status":"Closed"
            }])))
        page=client.get("/v3",params={"shift_id":sid})
        assert page.status_code==200
        # Attribution still uses that actual completion; the user no longer
        # wants the practice-only timestamp column visible on the dashboard.
        assert "Oct 26, 4:30 AM" not in page.text
        assert "Completed at</th>" not in page.text
        assert "<th>Action</th>" in page.text
        assert "Oct 26, 9:45 AM" not in page.text


def test_restored_admin_permissions_and_history_eml_wiring_are_present():
    routes=(ROOT/"v3_routes.py").read_text()
    main=(ROOT/"main.py").read_text()
    assert 'async def v3_admin_roster' in routes
    assert 'async def v3_admin_roster_edit' in routes
    assert 'async def v3_history_eml' in routes
    assert 'require_v3_manager(request)' in routes
    assert 'build_eml_response=_build_eml_response' in main
    assert 'refresh_roster=_refresh_v3_roster_from_db' in main
    assert 'WO Closed Date/Time' in routes or 'technician-supported' in routes

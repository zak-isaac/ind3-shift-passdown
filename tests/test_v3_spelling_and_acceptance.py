"""Acceptance regressions for V3 spelling, shift ownership, and closure reminders."""
import json
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

import db
from v3_cetaris import select_records
from v3_routes import create_v3_router
from v3_split import assign

ROOT = Path(__file__).resolve().parents[1]


def wo(number, tech, shift_start, returned, status="Closed", rfr="Breakdown"):
    return {
        "wo_number": str(number), "tech_name": tech, "asset_id": "VOIDFILL0501",
        "status": status, "rfr": rfr, "reported_issue": "Side guide",
        "remarks": "Checked colum tention", "opened_at": shift_start,
        "start_at": shift_start, "in_service_at": returned,
        "last_complete_at": "", "closed_at": returned if status == "Closed" else ""
    }


def test_shared_s3_s4_finish_before_0700_credits_s3_once():
    rows = [
        wo("28025279", "Tech S3", "2026-10-23T06:05:00", "2026-10-23T06:53:00"),
        wo("28025279", "Tech S4", "2026-10-23T06:15:00", "2026-10-23T06:53:00"),
    ]
    selected = {"Tech S3", "Tech S4"}
    roster = {"Tech S3": "S3", "Tech S4": "S4"}
    assert assign(rows,selected,"S3","2026-10-22",roster_shifts=roster)==(rows,[])
    assert assign(rows,selected,"S4","2026-10-23",roster_shifts=roster)==([],[])


def test_incoming_shift_selection_cannot_steal_shared_s3_wo():
    rows = [
        wo("28025284", "Tech S3", "2026-10-23T06:05:00", "2026-10-23T06:53:00"),
        wo("28025284", "Tech S4", "2026-10-23T06:15:00", "2026-10-23T06:53:00"),
    ]
    roster = {"Tech S3": "S3", "Tech S4": "S4"}
    # S4 MST selects only their own team, but the full Cetaris file still
    # contains the collaborating S3 labor; S4 may not claim the same WO.
    assert assign(rows,{"Tech S4"},"S4","2026-10-23",roster_shifts=roster)==([],[])
    assert assign(rows,{"Tech S3"},"S3","2026-10-22",roster_shifts=roster)==([rows[0]],[])


def test_shared_s3_s1_finishes_before_0700_credits_s3():
    rows = [
        wo("28025280", "Tech S3", "2026-10-20T06:05:00", "2026-10-20T06:35:00"),
        wo("28025280", "Tech S1", "2026-10-20T06:10:00", "2026-10-20T06:35:00"),
    ]
    roster = {"Tech S3": "S3", "Tech S1": "S1"}
    assert assign(rows,set(roster),"S3","2026-10-19",roster_shifts=roster)==(rows,[])
    assert assign(rows,set(roster),"S1","2026-10-20",roster_shifts=roster)==([],[])


def test_shared_s3_finished_after_0700_credits_arriving_shift():
    rows = [
        wo("28025281", "Tech S3", "2026-10-23T06:05:00", "2026-10-23T07:10:00"),
        wo("28025281", "Tech S4", "2026-10-23T06:15:00", "2026-10-23T07:10:00"),
    ]
    roster = {"Tech S3": "S3", "Tech S4": "S4"}
    assert assign(rows,set(roster),"S4","2026-10-23",roster_shifts=roster)==(rows,[])
    assert assign(rows,set(roster),"S3","2026-10-22",roster_shifts=roster)==([],[])


def test_s4_started_before_1800_but_finished_in_s5():
    rows = [wo("28025282", "Tech S4", "2026-10-23T17:45:00", "2026-10-23T18:15:00")]
    roster = {"Tech S4": "S4"}
    assert assign(rows,{"Tech S4"},"S5","2026-10-23",roster_shifts=roster)==(rows,[])
    assert assign(rows,{"Tech S4"},"S4","2026-10-23",roster_shifts=roster)==([],[])


def test_s4_started_and_finished_before_1800_stays_s4():
    rows = [wo("28025283", "Tech S4", "2026-10-23T17:45:00", "2026-10-23T17:50:00")]
    roster = {"Tech S4": "S4"}
    assert assign(rows,{"Tech S4"},"S4","2026-10-23",roster_shifts=roster)==(rows,[])
    assert assign(rows,{"Tech S4"},"S5","2026-10-23",roster_shifts=roster)==([],[])


def test_completed_pm_counts_without_closed_status_or_reminder():
    rows = [
        wo("201","Tech A","2026-10-22T17:00:00","2026-10-22T17:20:00",
           status="Closed",rfr="Preventive Maintenance"),
        wo("202","Tech A","2026-10-22T17:30:00","2026-10-22T17:45:00",
           status="Completed",rfr="Preventive Maintenance")
    ]
    results, summary = select_records(rows,{"Tech A"},None,None,
                                      closure_reviews={"202":"add"})
    assert summary["unique_pm_wos"] == 2
    assert summary["unique_happening_wos"] == 0
    assert {r["kind"] for r in results} == {"pm"}


@pytest.fixture
def workspace_client(tmp_path):
    db.set_db_path(tmp_path / "v3_check.db")
    db.init_db()
    sid = db.start_shift("2026-10-22","S2","Test MST")
    app = FastAPI()
    app.include_router(create_v3_router(
        templates=Jinja2Templates(directory=str(ROOT / "templates")),
        is_lead_authed=lambda req: req.cookies.get("lead_auth") == "test",
        can_manage_shift=lambda req,candidate: candidate == sid,
        resolve_my_shift=lambda req: db.get_shift(sid),
        shift_window=lambda shift,d: (datetime.combine(d,time(12)),
                                      datetime.combine(d,time(22,30))),
        lead_redirect=lambda dest: RedirectResponse("/lead/login",status_code=303)
    ))
    with TestClient(app) as client:
        client.cookies.set("lead_auth", "test")
        assert client.get("/v3").status_code == 200
        yield client, sid


def test_automatic_spelling_does_not_need_mst_approval(workspace_client):
    client, sid = workspace_client
    r = client.post("/v3/manual", data={
        "shift_id":str(sid), "unit":"VOIDFILL0501",
        "issue":"colum tention", "remarks":"checked colum tention; PLC RTS"})
    assert r.status_code == 200  # default client follows the Save redirect
    assert "Column tension" in r.text
    assert "Checked column tension; PLC RTS" in r.text
    assert "Check spelling" not in r.text
    saved = next(e for e in db.get_entries(sid) if e["unit"] == "VOIDFILL0501")
    assert "Checked column tension; PLC RTS" in saved["raw_remarks"]
    client.cookies.clear()
    assert client.post("/v3/manual", data={
        "shift_id":str(sid), "unit":"M1","issue":"colum"}).status_code == 401



def test_completed_pm_needs_no_closure_popup_and_counts_once(workspace_client):
    client, sid = workspace_client
    rows = [
        wo("301", "Tech A", "2026-10-22T17:30:00",
           "2026-10-22T17:45:00", status="Completed",
           rfr="Preventive Maintenance"),
        wo("301", "Tech A", "2026-10-22T17:30:00",
           "2026-10-22T17:45:00", status="Completed",
           rfr="Preventive Maintenance")
    ]
    token = uuid.uuid4().hex
    with db._conn() as c:
        c.execute("INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)",
                  (token,sid,"completed_pm.xlsx",datetime.now().isoformat(timespec="seconds"),json.dumps(rows)))
    response=client.post(f"/v3/import/{token}/confirm", data={
        "shift_id":str(sid), "techs":["Tech A"]},follow_redirects=False)
    assert response.status_code == 303
    assert db.get_pm_count(sid)==1
    with db._conn() as c:
        assert c.execute("SELECT count(*) FROM v3_pending_closure WHERE shift_id=?",
                         (sid,)).fetchone()[0]==0


def test_completed_wo_requires_two_choices_and_has_auditable_reminder(workspace_client):
    client, sid = workspace_client
    rows = [
        wo("201","Tech A","2026-10-22T17:00:00","2026-10-22T17:20:00",
           rfr="Preventive Maintenance"),
        wo("202","Tech A","2026-10-22T17:30:00","2026-10-22T17:45:00",
           status="Completed",rfr="Proactive Repair")
    ]
    token = uuid.uuid4().hex
    with db._conn() as c:
        c.execute("INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)",
                  (token,sid,"sample.xlsx",datetime.now().isoformat(timespec="seconds"),json.dumps(rows)))
    endpoint = f"/v3/import/{token}/confirm"
    preview = client.post(endpoint,data={"shift_id":str(sid),"techs":["Tech A"]})
    assert preview.status_code == 200
    assert "Add to Passdown" in preview.text
    assert "> Ignore" in preview.text
    assert db.get_pm_count(sid) == 0
    outcome = client.post(endpoint,data={"shift_id":str(sid),
        "techs":["Tech A"],"completed_wo_202":"add"},follow_redirects=False)
    assert outcome.status_code == 303
    assert db.get_pm_count(sid) == 1
    dashboard = client.get("/v3")
    assert "Completed · Close WO" in dashboard.text
    with db._conn() as c:
        audits = c.execute("SELECT wo_number,evidence_json FROM v3_wo_attribution_audit WHERE shift_id=?",
                           (sid,)).fetchall()
        assert {r["wo_number"] for r in audits} == {"201","202"}
        assert c.execute("SELECT decision FROM v3_pending_closure WHERE shift_id=? AND wo_number='202'",
                         (sid,)).fetchone()[0] == "add"

"""Regression checks for V3-only navigation and report/close lifecycle."""
from datetime import datetime, time, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import db
from v3_routes import create_v3_router

ROOT=Path(__file__).resolve().parents[1]

@pytest.fixture
def client_and_shift(tmp_path):
    db.set_db_path(tmp_path/"v3_migration.db")
    db.init_db()
    sid=db.start_shift("2026-10-20","S2","Test MST")
    app=FastAPI()
    async def fake_generate(request,shift_id,extra_title=None,extra_note=None,
                             vendor_name=None,ro_item=None,rts_unit=None):
        db.save_report(shift_id,"Test generated report","")
    async def fake_build_eml(report_id,subject,html_body):
        from fastapi.responses import Response
        return Response(content=f"eml-for-report-{report_id}".encode(),
                        media_type="message/rfc822",
                        headers={"Content-Disposition":'attachment; filename="passdown.eml"'})
    app.include_router(create_v3_router(
        templates=Jinja2Templates(directory=str(ROOT/"templates")),
        is_lead_authed=lambda req:req.cookies.get("lead_auth")=="test",
        can_manage_shift=lambda req,candidate:candidate==sid,
        resolve_my_shift=lambda req:db.get_shift(sid),
        shift_window=lambda shift,d:(datetime.combine(d,time(0)),datetime.combine(d+timedelta(days=1),time(0))),
        lead_redirect=lambda dest:RedirectResponse("/lead/login",status_code=303),
        generate_report=fake_generate,
        build_eml_response=fake_build_eml,
    ))
    with TestClient(app) as client:
        client.cookies.set("lead_auth","test")
        yield client,sid

def test_v3_navigation_and_pages(client_and_shift):
    client,sid=client_and_shift
    workspace=client.get("/v3",params={"shift_id":sid})
    assert workspace.status_code==200
    assert 'href="/v3/history?' in workspace.text
    assert 'href="/v3/analytics?' in workspace.text
    assert 'href="/v3/admin"' in workspace.text
    assert 'href="/admin"' not in workspace.text
    assert 'href="/history"' not in workspace.text
    assert 'href="/analytics"' not in workspace.text
    for page in ("history","analytics","operations","preview"):
        response=client.get("/v3/"+page,params={"shift_id":sid})
        assert response.status_code==200,(page,response.text[:400])
    assert "/v3/operations" in client.get("/v3/preview",params={"shift_id":sid}).text

def test_report_generation_then_shift_close(client_and_shift):
    client,sid=client_and_shift
    blocked=client.post("/v3/end",data={"shift_id":sid},follow_redirects=False)
    assert blocked.status_code==303
    assert db.get_shift(sid)["is_active"]
    generated=client.post("/v3/generate",data={"shift_id":sid},follow_redirects=False)
    assert generated.status_code==303
    report=db.get_last_report_for_shift(sid)
    assert report is not None
    assert "/v3/report/" in generated.headers["location"]
    report_view=client.get("/v3/report/"+str(report["id"]))
    assert report_view.status_code==200
    assert "Test generated report" in report_view.text
    closed=client.post("/v3/end",data={"shift_id":sid},follow_redirects=False)
    assert closed.status_code==303
    assert not db.get_shift(sid)["is_active"]
    assert client.get("/v3/ended",params={"shift_id":sid}).status_code==200
    assert client.get("/v3/report/"+str(report["id"])).status_code==200


def test_send_builds_report_and_returns_eml_in_one_hop(client_and_shift):
    """'Generate & Send' must not bounce through /v3/operations or /v3/report --
    one GET should both create the report and hand back the downloadable .eml."""
    client,sid=client_and_shift
    assert db.get_last_report_for_shift(sid) is None
    response=client.get("/v3/send",params={"shift_id":sid},follow_redirects=False)
    assert response.status_code==200
    assert response.headers["content-type"]=="message/rfc822"
    assert "attachment" in response.headers["content-disposition"]
    report=db.get_last_report_for_shift(sid)
    assert report is not None
    assert response.content==f"eml-for-report-{report['id']}".encode()

def test_unauthorized_v3_records_blocked(client_and_shift):
    client,sid=client_and_shift
    client.cookies.clear()
    assert client.get("/v3/history",follow_redirects=False).status_code==303
    assert client.get("/v3/analytics",follow_redirects=False).status_code==303
    assert client.post("/v3/generate",data={"shift_id":sid},follow_redirects=False).status_code==401
    assert client.post("/v3/end",data={"shift_id":sid},follow_redirects=False).status_code==401
    assert client.get("/v3/admin").status_code==403

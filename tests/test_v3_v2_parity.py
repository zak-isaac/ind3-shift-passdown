"""Current V3 retains operational shift tools while enforcing MST-only access.

This replaces older tests expecting a V2 logo and non-MST technicians to
start shifts. The live product uses V3-only login and role boundaries.
"""
from datetime import datetime, time, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import db
from v3_routes import create_v3_router

ROOT=Path(__file__).resolve().parents[1]


def test_v3_mst_only_login_shift_isolation_and_manager_selection(tmp_path):
    db.set_db_path(tmp_path/"isolated.db");db.init_db()
    roster={
        "1001":{"display_name":"First MST","is_mst":True,
                "is_manager":False,"shift":"S2"},
        "2002":{"display_name":"Non-MST Technician","is_mst":False,
                "is_manager":False,"shift":"S3"},
        "3003":{"display_name":"Second MST","is_mst":True,
                "is_manager":False,"shift":"S3"},
        "8972":{"display_name":"Manager","is_mst":False,
                "is_manager":True,"shift":"ALL"}
    }
    app=FastAPI()
    app.mount("/static",StaticFiles(directory=str(ROOT/"static")),name="static")
    app.include_router(create_v3_router(
        templates=Jinja2Templates(directory=str(ROOT/"templates")),
        is_lead_authed=lambda _:False,
        can_manage_shift=lambda _req,_sid:False,
        resolve_my_shift=lambda _:None,
        shift_window=lambda _code,day:(
            datetime.combine(day,time(0)),
            datetime.combine(day+timedelta(days=1),time(0))),
        lead_redirect=lambda _:RedirectResponse("/v3/login",status_code=303),
        get_mst_roster=lambda:roster,
        start_eligibility=lambda person,code,now:(
            person.get("shift")==code,False,"Wrong assignment"),
        allowed_shift_days={"S2":{1},"S3":{1}},
        now_func=lambda:datetime(2026,10,20,21,0),
    ))
    with TestClient(app) as browser:
        page=browser.get("/v3/login")
        assert page.status_code==200
        assert "walmart-wordmark-cropped.svg" in page.text
        assert 'name="pin"' not in page.text
        assert 'name="associate_id"' in page.text

        # Recognized manager receives the V3 shift-selector, not a V2 login.
        manager=browser.post("/v3/login",data={
            "associate_id":"8972","mode":"start"},follow_redirects=False)
        assert manager.status_code==200
        assert 'master-shift-dialog' in manager.text
        browser.get("/v3/logout")

        started=browser.post("/v3/login",data={
            "associate_id":"1001","mode":"start"},follow_redirects=False)
        assert started.status_code==303
        shifts=db.get_active_shifts()
        assert len(shifts)==1
        sid1=shifts[0]["id"]
        assert db.get_shift(sid1)["opened_by_role"]=="mst"
        assert db.get_shift(sid1)["opened_by_assoc"]=="1001"
        assert browser.get(f"/v3/operations?shift_id={sid1}").status_code==200
        browser.get("/v3/logout")

        # Technician participation in a WO does not authorize starting or
        # resuming the MST shift without an MST role.
        denied=browser.post("/v3/login",data={
            "associate_id":"2002","mode":"resume","shift_id":str(sid1)})
        assert denied.status_code==200
        assert "Only authorized MST associates" in denied.text
        rejected=browser.post("/v3/login",data={
            "associate_id":"2002","mode":"start"})
        assert "Only authorized MST associates" in rejected.text

        second=browser.post("/v3/login",data={
            "associate_id":"3003","mode":"start"},follow_redirects=False)
        assert second.status_code==303
        sid2=next(s["id"] for s in db.get_active_shifts()
                  if s["shift_type"]=="S3")
        assert db.get_shift(sid2)["opened_by_assoc"]=="3003"
        assert browser.get(f"/v3/operations?shift_id={sid2}").status_code==200
        assert browser.get(f"/v3/operations?shift_id={sid1}").status_code==403
        assert browser.post("/v3/manual",data={
            "shift_id":sid1,"unit":"M1","issue":"cross-shift"
        },follow_redirects=False).status_code==403

        browser.get("/v3/logout")
        resumed=browser.post("/v3/login",data={
            "associate_id":"1001","mode":"resume","shift_id":str(sid1)
        },follow_redirects=False)
        assert resumed.status_code==303
        assert resumed.headers["location"]==f"/v3?shift_id={sid1}"
        assert browser.get(f"/v3/operations?shift_id={sid1}").status_code==200
        assert browser.get(f"/v3/operations?shift_id={sid2}").status_code==403


def test_personal_override_2759_gets_elevated_rights_without_manager_flag(tmp_path):
    """2026-10-26: 2759 is Isaac's own associate number, not a shared manager
    account. It must get the same elevated/override rights as the 8972
    master code (per main.py's _MASTER_CODES and v3_identity.py's elevated
    session check), but its roster record itself must stay is_manager=False
    -- the elevation is granted at the session level only, exactly like
    production's real roster row for 2759.
    """
    db.set_db_path(tmp_path/"override.db");db.init_db()
    roster={
        "2759":{"display_name":"Isaac S","full_name":"Isaac Ssaku",
                "is_mst":False,"is_manager":False,"shift":"S2"},
        "1001":{"display_name":"Ordinary MST","is_mst":True,
                "is_manager":False,"shift":"S2"},
    }
    app=FastAPI()
    app.mount("/static",StaticFiles(directory=str(ROOT/"static")),name="static")
    app.include_router(create_v3_router(
        templates=Jinja2Templates(directory=str(ROOT/"templates")),
        is_lead_authed=lambda _:False,
        can_manage_shift=lambda _req,_sid:False,
        resolve_my_shift=lambda _:None,
        shift_window=lambda _code,day:(
            datetime.combine(day,time(0)),
            datetime.combine(day+timedelta(days=1),time(0))),
        lead_redirect=lambda _:RedirectResponse("/v3/login",status_code=303),
        get_mst_roster=lambda:roster,
        start_eligibility=lambda person,code,now:(
            person.get("shift")==code,False,"Wrong assignment"),
        allowed_shift_days={"S2":{1}},
        now_func=lambda:datetime(2026,10,20,21,0),
    ))
    with TestClient(app) as browser:
        # The roster record itself is never a manager.
        assert roster["2759"]["is_manager"] is False

        # 2759 gets the same master-shift-dialog override entry as 8972,
        # despite its roster record saying is_manager=False.
        override=browser.post("/v3/login",data={
            "associate_id":"2759","mode":"start"},follow_redirects=False)
        assert override.status_code==200
        assert "master-shift-dialog" in override.text

        # Manager-only admin surface is reachable with the elevated session.
        assert browser.get("/v3/admin/roster").status_code==200
        browser.get("/v3/logout")

        # An ordinary MST without the override code is correctly denied.
        started=browser.post("/v3/login",data={
            "associate_id":"1001","mode":"start"},follow_redirects=False)
        assert started.status_code==303
        assert browser.get("/v3/admin/roster",follow_redirects=False).status_code==403

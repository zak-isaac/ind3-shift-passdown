"""V3 release acceptance: analytics from uploaded Cetaris records and streamlined UI.

Fixtures intentionally use synthetic WOs and technicians. No actual facility
Cetaris export, associates, credentials or production DB is committed.
"""
from datetime import datetime, time, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates

import db
from v3_cetaris_analytics import get_cetaris_analytics
from v3_routes import create_v3_router

ROOT = Path(__file__).resolve().parents[1]


def test_analytics_uses_unique_cetaris_wos_and_completed_pm_not_priority(tmp_path):
    db.set_db_path(tmp_path/"cetaris_metrics.db")
    db.init_db()
    s4=db.start_shift("2026-10-24","S4","MST A")
    s5=db.start_shift("2026-10-24","S5","MST B")
    with db._conn() as c:
        c.execute("CREATE TABLE v3_entry_meta(entry_id INTEGER PRIMARY KEY,rfr_type TEXT)")
    def add(sid,wo,asset,tech,kind,excluded=False):
        eid=db.upsert_cmms_entry(sid,wo,asset,"Issue","[]",tech)
        with db._conn() as c:
            c.execute("INSERT INTO v3_entry_meta(entry_id,rfr_type) VALUES (?,?)",(eid,kind))
            c.execute("UPDATE entries SET priority='down',excluded=? WHERE id=?",(int(excluded),eid))
        return eid
    add(s4,"WO-A","BAG0005","Tech A","Process Interruption")
    add(s4,"WO-A","BAG0005","Tech B","Process Interruption")
    add(s4,"WO-B","BAG0005","Tech A","Breakdown")
    add(s4,"WO-IGNORE","MDR0001","Tech C","Breakdown",excluded=True)
    db.add_entry(s4,"Manual MST","VOIDFILL0101","Manual issue","Did work","","down")
    db.upsert_pm_wo(s4,"PM-1","MDR0001","Tech A")
    db.upsert_pm_wo(s4,"PM-1","MDR0001","Tech A")
    db.upsert_pm_wo(s5,"PM-2","VOIDFILL0301","Tech B")
    data=get_cetaris_analytics()
    assert data["totals"]=={
        "total_shifts":2,"total_entries":2,"total_pms":2,"breakdown_wos":1}
    assert data["categories"]==[
        {"name":"Breakdown","cnt":1},{"name":"Process Interruption","cnt":1}]
    assert data["top_assets"][0]=={"unit":"BAG0005","cnt":2}
    assert data["repeat_assets"][0]=={"unit":"BAG0005","shift_cnt":1,"total_wo":2}
    assert data["top_techs"][0]=={"tech_name":"Tech A","cnt":2}
    assert {"tech_name":"Tech B","cnt":1} in data["top_techs"]
    assert {(r["shift_type"],r["wo_count"],r["pm_count"])
            for r in data["wo_by_shift"]}=={("S4",2,1),("S5",0,1)}
    assert "down" not in data and "priority_totals" not in data


def test_analytics_handles_fresh_db_without_v3_metadata(tmp_path):
    db.set_db_path(tmp_path/"fresh_analytics.db");db.init_db()
    db.start_shift("2026-10-24","S4","MST A")
    assert get_cetaris_analytics()["totals"]["total_entries"]==0
    assert get_cetaris_analytics()["totals"]["total_shifts"]==0


def test_workspace_has_one_file_card_and_five_columns_without_debug_labels():
    workspace=(ROOT/"templates/v3/workspace.html").read_text(encoding="utf-8")
    assert workspace.count("Change technicians</a>")==1
    assert "d2-import-summary card" not in workspace
    assert "d2-import-flow card" in workspace
    # 2026-10-26: Isaac explicitly asked for the detailed read-only ignored-WO
    # audit drawer back (it was hidden by an earlier same-night simplification
    # pass) -- these two assertions used to lock that drawer OUT, now they
    # confirm it's present. The five-column happenings table itself (checked
    # below) is unrelated and unchanged.
    assert "Show {{ import_audit.ignored_count }}" in workspace
    assert "d2-audit-details" in workspace
    assert "<th>Asset ID</th><th>Type</th><th>Cause</th><th>Tech Remarks</th><th>Action</th>" in workspace
    assert "d2-asset-wo" in workspace
    assert "<th>Completed at</th>" not in workspace
    # 2026-10-26: the restored Imported WO audit drawer legitimately has its
    # own "WO #" column header (a separate table from Shift Happenings,
    # which is already fully verified five-column via the assertion above).
    assert "AI Passed" not in workspace
    assert "Completed · Close WO" not in workspace
    assert "data-ai-remark-wo" in workspace
    assert "/v3/ai/drafts?" in workspace  # AI still rewrites text automatically
    analytics=(ROOT/"templates/v3/analytics.html").read_text(encoding="utf-8")
    assert "priority_totals" not in analytics
    assert "down_count" not in analytics
    assert "Down events" not in analytics
    assert "chartCategories" in analytics and "chartPMTrend" in analytics
    topbar=(ROOT/"templates/v3/_topbar.html").read_text(encoding="utf-8")
    assert "Shift owner" not in topbar and "Maintenance" not in topbar
    assert "/static/v3_live_clock.js" in topbar
    login=(ROOT/"templates/v3/login.html").read_text(encoding="utf-8")
    assert "→" not in login


def test_manager_resume_selected_shift_opens_workspace_without_choose_again(tmp_path):
    db.set_db_path(tmp_path/"manager_resume.db");db.init_db()
    s4=db.start_shift("2026-10-24","S4","MST First",opened_by_assoc="msta")
    s5=db.start_shift("2026-10-24","S5","MST Second",opened_by_assoc="mstb")
    roster={
        "8972":{"display_name":"Manager","full_name":"Manager",
                 "is_manager":True,"is_mst":False,"shift":"ALL"}
    }
    def window(code,day):
        start=time(5,30) if code=="S4" else time(17,30)
        end=datetime.combine(day,time(19)) if code=="S4" else (
            datetime.combine(day+timedelta(days=1),time(7)))
        return datetime.combine(day,start),end
    app=FastAPI()
    app.include_router(create_v3_router(
        templates=Jinja2Templates(directory=str(ROOT/"templates")),
        is_lead_authed=lambda req: False,
        can_manage_shift=lambda req,sid:False,
        resolve_my_shift=lambda req:None,
        shift_window=window,
        lead_redirect=lambda dest:RedirectResponse("/v3/login",status_code=303),
        get_mst_roster=lambda:roster,
        start_eligibility=lambda p,t,n:(True,False,""),
        allowed_shift_days={"S4":{5},"S5":{5}},
        now_func=lambda:datetime(2026,10,24,18,10)))
    with TestClient(app) as client:
        posted=client.post("/v3/login",data={
            "associate_id":"8972","mode":"resume","shift_id":str(s5)
        },follow_redirects=False)
        assert posted.status_code==303
        assert posted.headers["location"]==f"/v3?shift_id={s5}"
        assert "master-shift-dialog" not in posted.text
        dashboard=client.get(f"/v3?shift_id={s5}")
        assert dashboard.status_code==200, dashboard.text[:300]
        assert "S5 · 2026-10-24" in dashboard.text
        assert "Shift happenings" in dashboard.text
        # A different selected active shift can be resumed without double-chooser.
        client.get("/v3/logout")
        posted2=client.post("/v3/login",data={
            "associate_id":"8972","mode":"resume","shift_id":str(s4)
        },follow_redirects=False)
        assert posted2.headers["location"]==f"/v3?shift_id={s4}"


def test_outlook_signature_matches_real_corporate_format():
    """2026-10-28: Isaac later provided a screenshot of his actual real
    corporate Outlook signature and asked for 'something like this' instead
    of the invented IND3 Next Gen card branding -- name-ID, facility number,
    address, then the Walmart tagline. This supersedes the brief earlier
    rollback to the old bordered card (same day); that design is gone too.
    """
    import signatures
    output=signatures.get_signature_html("Isaac Ssaku","S5")
    assert "Isaac Ssaku - ins0002" in output
    assert "eCommerce Fulfillment Center # 7552/8972" in output
    assert "5259 W. 500 North McCordsville, Indiana, 46055" in output
    assert "cid:ind3-walmart-spark" in output
    assert "Save money." in output and "Live better" in output
    # Role/shift is accepted for backward compatibility but not rendered.
    assert "Maintenance Technician" not in output
    assert "S5" not in output

"""E2E V3 route test. All records are synthetic; AI and Outlook are not contacted."""
import json
import re
import time
from datetime import datetime, time as hour, timedelta
from email import policy
from email.parser import BytesParser
from io import BytesIO
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from openpyxl import Workbook
import db
import main
import v3_ollama_background as ai
from v3_routes import create_v3_router

ROOT = Path(__file__).resolve().parents[1]
HEADERS = ["WO Number","Asset #","WO Status","Primary RFR",
    "Service/Repair Description","Technician","Technician Remarks",
    "Start Date","WO Date / Time","WO Last Complete Date / Time",
    "WO Closed Date/Time","In Service Date / Time","Last Shift"]


def labor(wo, tech, stamp, notes, status="Closed", rfr="Proactive Repair"):
    t = datetime.fromisoformat(stamp)
    return [wo,"M"+wo,status,rfr,"Side guide not gripping boxes",tech,notes,
            t,t-timedelta(minutes=5),t+timedelta(minutes=10),
            t+timedelta(minutes=15) if status=="Closed" else None,
            t+timedelta(minutes=10),"S4"]


def workbook():
    b=Workbook(); s=b.active;s.append(HEADERS)
    s.append(labor("28030001","Carr, Alexander","2026-10-25T08:30:00","checked colum tention"))
    s.append(labor("28030001","Spires, Bradley","2026-10-25T08:35:00","Recalibrated side guide. RTS"))
    s.append(labor("28030002","Koukpo, Kpedekpo","2026-10-25T09:00:00","Completed PM inspection",status="Completed",rfr="Preventive Maintenance"))
    s.append(labor("28030003","Carr, Alexander","2026-09-01T09:00:00","Historical work order"))
    s.append(labor("28030004","Rob Tice","2026-10-25T09:15:00","Still in process",status="In Process"))
    s.append(labor("28030005","Carr, Alexander","2026-10-25T09:30:00","Replaced side guide",status="Completed"))
    out=BytesIO();b.save(out);return out.getvalue()


def make_app(roster):
    app=FastAPI()
    app.mount("/static",StaticFiles(directory=str(ROOT/"static")),name="static")
    app.include_router(create_v3_router(
        templates=Jinja2Templates(directory=str(ROOT/"templates")),
        is_lead_authed=main._is_lead_authed,
        can_manage_shift=main._can_manage_shift,
        resolve_my_shift=main._resolve_my_shift,
        shift_window=lambda code,d:(datetime.combine(d,hour(5,30)),datetime.combine(d,hour(18))),
        lead_redirect=lambda _:RedirectResponse("/v3/login",status_code=303),
        get_mst_roster=lambda:roster,
        start_eligibility=lambda person,shift,now:(True,False,""),
        allowed_shift_days={"S4":{6}},
        now_func=lambda:datetime(2026,10,25,15,0),
        generate_report=main.lead_generate))
    @app.get("/report/{report_id}/compose-outlook")
    async def eml(report_id:int):
        return await main._build_eml_response(report_id,"","")
    return app


def test_full_login_import_ai_preview_and_eml(tmp_path,monkeypatch):
    db.set_db_path(tmp_path/"v3_workflow.db");db.init_db()
    roster={
      "1001":{"full_name":"MST Test","display_name":"MST Test","is_mst":True,"is_manager":False,"shift":"S4"},
      "2001":{"full_name":"Carr, Alexander","display_name":"Carr, Alexander","is_mst":False,"shift":"S4"},
      "2002":{"full_name":"Spires, Bradley","display_name":"Spires, Bradley","is_mst":False,"shift":"S4"},
      "2003":{"full_name":"Koukpo, Kpedekpo","display_name":"Koukpo, Kpedekpo","is_mst":False,"shift":"S4"},
    }
    monkeypatch.setattr(main,"_ROSTER",roster)
    monkeypatch.setenv("IND3_OLLAMA_REWRITE","1")
    monkeypatch.setenv("IND3_OLLAMA_MODEL","qwen2.5:3b")
    def mock_local_ai(item):
        assert json.loads(item["source_json"])
        if item["wo_number"]=="28030001":
            return "Checked column tension and recalibrated the side guide. RTS.","passed","Automated checks passed"
        return "","original","Original remarks retained"
    monkeypatch.setattr(ai,"_generate",mock_local_ai)
    with TestClient(make_app(roster)) as browser:
        login=browser.get("/v3/login")
        assert login.status_code==200 and 'name="associate_id"' in login.text
        started=browser.post("/v3/login",data={"associate_id":"1001","mode":"start"},follow_redirects=False)
        assert started.status_code==303,started.text[:350]
        sid=int(started.headers["location"].split("shift_id=")[-1])
        assert db.get_shift(sid)["opened_by_assoc"]=="1001"
        assert browser.get("/v3",params={"shift_id":sid}).status_code==200
        staged=browser.post("/v3/import",data={"shift_id":str(sid)},
            files={"file":("cetaris_test.xlsx",workbook(),
              "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert staged.status_code==200,staged.text[:500]
        assert "5 unique WOs" in staged.text
        assert "Carr, Alexander" in staged.text and "Spires, Bradley" in staged.text
        token=re.search(r"/v3/import/([0-9a-f]{32})/confirm",staged.text).group(1)
        techs=["Carr, Alexander","Spires, Bradley","Koukpo, Kpedekpo"]
        review=browser.post(f"/v3/import/{token}/confirm",data={"shift_id":str(sid),"techs":techs},follow_redirects=False)
        assert review.status_code==200,review.text[:500]
        assert "Completed work orders need Cetaris closure" in review.text
        finish=browser.post(f"/v3/import/{token}/confirm",
            data={"shift_id":str(sid),"techs":techs,"completed_wo_28030005":"add"},follow_redirects=False)
        assert finish.status_code==303,finish.text[:500]
        assert {x["wo_number"] for x in db.get_entries(sid)}=={"28030001","28030005"}
        assert db.get_pm_count(sid)==1
        deadline=time.monotonic()+6
        while time.monotonic()<deadline:
            state={x["wo_number"]:x for x in ai.statuses(sid)}
            if state.get("28030001",{}).get("status")=="passed":break
            time.sleep(.03)
        assert state.get("28030001",{}).get("status")=="passed",state
        page=browser.get("/v3",params={"shift_id":sid})
        assert page.status_code==200
        # 2026-10-26: the interactive Needs Review Include/Dismiss panel was
        # removed at Isaac's request the same day. Ignored WOs are visible
        # again only in the restored read-only ignored-WO audit drawer, and
        # must never be counted as a real Shift Happening or PM entry.
        assert "28030003" not in {x["wo_number"] for x in db.get_entries(sid)}
        assert "Imported WO audit" in page.text and "28030003" in page.text
        rewritten="Checked column tension and recalibrated the side guide. RTS."
        assert rewritten in page.text
        assert 'data-ai-remark-wo="28030001"' in page.text
        assert "AI Passed" not in page.text  # No debugging badges in the production table
        assert "Completed · Close WO" not in page.text  # Practice-only badge removed
        assert "<th>Action</th>" in page.text
        entry=next(e for e in db.get_entries(sid) if e["wo_number"]=="28030001")
        detail=browser.get(f"/v3/happening/{entry['id']}",params={"shift_id":sid})
        assert detail.status_code==200 and "Checked column tension" in detail.text
        assert any(x["status"]=="passed" for x in browser.get("/v3/ai/drafts",params={"shift_id":sid}).json()["drafts"])
        preview=browser.get("/v3/preview",params={"shift_id":sid})
        assert preview.status_code==200 and rewritten in preview.text
        saved_response=browser.post("/v3/generate",data={"shift_id":str(sid)},follow_redirects=False)
        assert saved_response.status_code==303,saved_response.text[:500]
        saved=db.get_last_report_for_shift(sid)
        assert saved and saved.get("report_json")
        from report_models import ReportData
        structured=ReportData.model_validate_json(saved["report_json"])
        assert structured.pm_count==1
        assert any(rewritten in h.paragraph for h in structured.happenings)
        response=browser.get(f"/report/{saved['id']}/compose-outlook")
        assert response.status_code==200,response.text[:300]
        msg=BytesParser(policy=policy.default).parsebytes(response.content)
        assert msg["X-Unsent"]=="1"
        assert msg["Subject"]=="S4 Passdown"
        html="".join(p.get_content() for p in msg.walk() if p.get_content_type()=="text/html")
        assert rewritten in html
        assert "M28030001" in html
        assert "Save money." in html and "Live better" in html
        assert "colum tention" not in html
        assert db.get_shift(sid)["is_active"]


def test_separate_eml_export_is_draft_compatible():
    raw=main._build_eml("S4 Passdown","test@example.invalid","<html><body>Review only</body></html>")
    msg=BytesParser(policy=policy.default).parsebytes(raw)
    assert msg["X-Unsent"]=="1"
    assert msg["To"]=="test@example.invalid"
    html="".join(p.get_content() for p in msg.walk() if p.get_content_type()=="text/html")
    assert "Review only" in html

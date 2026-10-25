"""End-to-end S5 cross-midnight Cetaris import and partial duplicate recovery."""
from datetime import datetime, time, timedelta
from io import BytesIO
from pathlib import Path
import re

from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
from openpyxl import Workbook

import db
from v3_routes import create_v3_router

ROOT = Path(__file__).resolve().parents[1]
HEADERS = [
    "WO Number", "Asset #", "WO Status", "Primary RFR",
    "Service/Repair Description", "Technician", "Technician Remarks",
    "Start Date", "WO Date / Time", "WO Last Complete Date / Time",
    "WO Closed Date/Time", "In Service Date / Time", "Last Shift"
]

def stamp(s):
    return datetime.fromisoformat(s) if s else None

def labor(wo, start, completed, service=None, category="Proactive Repair",
          tech="Tech S5", status="Closed", opened=None):
    return [
        wo, "M" + wo, status, category, "Maintenance work", tech,
        "Completed checks; RTS", stamp(start), stamp(opened or start),
        stamp(completed), stamp(completed), stamp(service), "S5"
    ]

def test_s5_saturday_shift_credits_sunday_early_work_and_imports_nonduplicates(tmp_path):
    db.set_db_path(tmp_path / "s5_overnight.db")
    db.init_db()
    shift_id = db.start_shift("2026-10-24", "S5", "Weekend MST")
    other = db.start_shift("2026-10-24", "S4", "Previous MST")
    db.upsert_pm_wo(other, "DUP-PM", "MDUP-PM", "Tech S4")

    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    app.include_router(create_v3_router(
        Jinja2Templates(directory=str(ROOT / "templates")),
        is_lead_authed=lambda request: request.cookies.get("lead_auth") == "test",
        can_manage_shift=lambda request, sid: sid == shift_id,
        resolve_my_shift=lambda request: db.get_shift(shift_id),
        shift_window=lambda code, day: (
            datetime.combine(day, time(17, 30)),
            datetime.combine(day + timedelta(days=1), time(7))
        ),
        lead_redirect=lambda dest: RedirectResponse("/v3/login", status_code=303),
        now_func=lambda: datetime(2026, 10, 25, 2, 30)
    ))

    book = Workbook()
    sheet = book.active
    sheet.append(HEADERS)
    # Work from S5's evening portion.
    sheet.append(labor("S5-EVE", "2026-10-24T20:10:00",
                       "2026-10-24T20:35:00", "2026-10-24T20:30:00"))
    # Sunday 01:00 still belongs to Saturday S5.
    sheet.append(labor("S5-AM", "2026-10-25T01:10:00",
                       "2026-10-25T01:20:00", "2026-10-25T01:18:00"))
    # No labor start or In Service: WO Last Complete is enough evidence.
    sheet.append(labor("S5-PM", None, "2026-10-25T02:00:00",
                       category="Preventive Maintenance",
                       opened="2026-10-24T12:00:00"))
    # Older WO start + next-day completion must be shown for MST review.
    sheet.append(labor("S5-REVIEW", "2026-10-22T14:00:00",
                       "2026-10-25T02:15:00", opened="2026-10-22T13:00:00"))
    # Previously credited WO: skip it, not the rest of the file.
    sheet.append(labor("DUP-PM", "2026-10-25T01:40:00",
                       "2026-10-25T01:50:00", "2026-10-25T01:49:00",
                       category="Preventive Maintenance"))
    buffer = BytesIO()
    book.save(buffer)

    with TestClient(app) as client:
        client.cookies.set("lead_auth", "test")
        stage=client.post("/v3/import",data={"shift_id":str(shift_id)},
             files={"file":("S5_both_dates.xlsx",buffer.getvalue(),
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert stage.status_code==200,stage.text[:700]
        assert "5 unique WOs" in stage.text
        token=re.search(r"/v3/import/([0-9a-f]{32})/confirm",stage.text).group(1)
        review=client.post(f"/v3/import/{token}/confirm",
             data={"shift_id":str(shift_id),"techs":["Tech S5"]},follow_redirects=False)
        assert review.status_code==200,review.text[:700]
        assert "S5-REVIEW" in review.text
        done=client.post(f"/v3/import/{token}/confirm",
             data={"shift_id":str(shift_id),"techs":["Tech S5"],
                   "split_wo_S5-REVIEW":"S5"},follow_redirects=False)
        assert done.status_code==303,done.text[:700]
        screen=client.get(f"/v3?shift_id={shift_id}")
        assert screen.status_code==200
        assert "Already credited to another shift (not counted)" in screen.text
        assert "S5-AM" in screen.text
        assert "S5-REVIEW" in screen.text
        assert {e["wo_number"] for e in db.get_entries(shift_id)} == {
            "S5-EVE","S5-AM","S5-REVIEW"
        }
        assert db.get_pm_count(shift_id)==1
        assert db.get_pm_count(other)==1

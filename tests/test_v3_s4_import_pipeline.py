"""End-to-end S4 Cetaris import with real shift timestamps and rejected older WOs."""
from datetime import datetime, date, time, timedelta
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
    "WO Closed Date/Time", "In Service Date / Time", "Last Shift",
]

def labor(wo, tech, start, status="Closed"):
    dt = datetime.fromisoformat(start)
    return [
        wo, "M" + wo, status, "Proactive Repair",
        "Service call", tech, "Inspected component and tested equipment",
        dt, dt - timedelta(minutes=5), dt + timedelta(minutes=10),
        dt + timedelta(minutes=15), dt + timedelta(minutes=10), "S4"
    ]

def test_s4_three_closed_wos_and_older_ignored_wo_are_visible(tmp_path):
    db.set_db_path(tmp_path / "pipeline.db")
    db.init_db()
    shift_id = db.start_shift("2026-10-25", "S4", "Test MST")
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    app.include_router(create_v3_router(
        Jinja2Templates(directory=str(ROOT / "templates")),
        is_lead_authed=lambda request: request.cookies.get("lead_auth") == "test",
        can_manage_shift=lambda request, sid: sid == shift_id,
        resolve_my_shift=lambda request: db.get_shift(shift_id),
        shift_window=lambda st,day: (
            datetime.combine(day,time(5,30)), datetime.combine(day,time(18,0))
        ),
        lead_redirect=lambda dest: RedirectResponse("/v3/login", status_code=303),
        now_func=lambda: datetime(2026,10,25,15,0)
    ))
    book = Workbook()
    sheet = book.active
    sheet.append(HEADERS)
    sheet.append(labor("28025869", "Carr, Alexander", "2026-10-25T08:30:00"))
    sheet.append(labor("28025965", "Koukpo, Kpedekpo", "2026-10-25T08:40:00"))
    sheet.append(labor("28025970", "Carr, Alexander", "2026-10-25T08:50:00"))
    sheet.append(labor("28025970", "Spires, Bradley", "2026-10-25T08:51:00"))
    sheet.append(labor("28025555", "Carr, Alexander", "2026-09-02T08:50:00"))
    sheet.append(labor("28025556", "Carr, Alexander", "2026-10-25T09:10:00",status="In Process"))
    buf=BytesIO()
    book.save(buf)
    with TestClient(app) as client:
        client.cookies.set("lead_auth","test")
        stage=client.post("/v3/import",data={"shift_id":str(shift_id)},
            files={"file":("S4_fixture.xlsx",buf.getvalue(),"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
        assert stage.status_code==200,stage.text[:700]
        assert "6 unique WOs" not in stage.text  # 5 total source WOs
        assert "5 unique WOs" in stage.text
        token=re.search(r"/v3/import/([0-9a-f]{32})/confirm",stage.text).group(1)
        confirm=client.post(f"/v3/import/{token}/confirm",data={
            "shift_id":str(shift_id),
            "techs":["Carr, Alexander","Spires, Bradley","Koukpo, Kpedekpo"],
        },follow_redirects=False)
        assert confirm.status_code==303,confirm.text[:700]
        assert "/v3?shift_id" in confirm.headers["location"]
        screen=client.get(f"/v3?shift_id={shift_id}")
        assert screen.status_code==200,screen.text[:800]
        assert "Imported WO audit" in screen.text
        # 2026-10-26: this WO has genuinely ambiguous evidence (old September
        # labor date). The interactive Needs Review Include/Dismiss panel
        # was removed at Isaac's request the same day; it's visible again
        # only in the restored read-only ignored-WO audit drawer, and must
        # never be counted as a real entry.
        assert "28025555" not in {x["wo_number"] for x in db.get_entries(shift_id)}
        assert "Imported WO audit" in screen.text and "28025555" in screen.text
        assert "Outside this shift" in screen.text
        with db._conn() as c:
            audit=c.execute("SELECT audit_json FROM v3_import_audit WHERE shift_id=?",(shift_id,)).fetchone()
        assert audit and "28025555" in audit["audit_json"]
        assert "28025869" in screen.text
        assert "28025965" in screen.text
        assert "28025970" in screen.text
        assert len({e["wo_number"] for e in db.get_entries(shift_id)})==3


def test_import_rejects_by_content_not_filename_extension(tmp_path):
    db.set_db_path(tmp_path / "content_validation.db")
    db.init_db()
    shift_id = db.start_shift("2026-10-25", "S4", "Test MST")
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    app.include_router(create_v3_router(
        Jinja2Templates(directory=str(ROOT / "templates")),
        is_lead_authed=lambda request: request.cookies.get("lead_auth") == "test",
        can_manage_shift=lambda request, sid: sid == shift_id,
        resolve_my_shift=lambda request: db.get_shift(shift_id),
        shift_window=lambda st, day: (
            datetime.combine(day, time(5, 30)), datetime.combine(day, time(18, 0))
        ),
        lead_redirect=lambda dest: RedirectResponse("/v3/login", status_code=303),
        now_func=lambda: datetime(2026, 10, 25, 15, 0)
    ))
    book = Workbook()
    sheet = book.active
    sheet.append(HEADERS)
    sheet.append(labor("28025869", "Carr, Alexander", "2026-10-25T08:30:00"))
    buf = BytesIO()
    book.save(buf)
    with TestClient(app) as client:
        client.cookies.set("lead_auth", "test")
        accepted = client.post("/v3/import", data={"shift_id": str(shift_id)},
            files={"file": ("export_from_kiosk_no_ext", buf.getvalue(), "application/octet-stream")})
        assert accepted.status_code == 200, accepted.text[:500]
        assert "1 unique WOs" in accepted.text
        rejected = client.post("/v3/import", data={"shift_id": str(shift_id)},
            files={"file": ("not_actually_cetaris.xlsx", b"just some random bytes", "application/octet-stream")})
        assert rejected.status_code == 200
        empty = client.post("/v3/import", data={"shift_id": str(shift_id)})
        assert empty.status_code == 200
        assert "No file was selected" in empty.text


def test_import_accepts_csv_export_same_as_xlsx(tmp_path):
    db.set_db_path(tmp_path / "csv_import.db")
    db.init_db()
    shift_id = db.start_shift("2026-10-25", "S4", "Test MST")
    app = FastAPI()
    app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
    app.include_router(create_v3_router(
        Jinja2Templates(directory=str(ROOT / "templates")),
        is_lead_authed=lambda request: request.cookies.get("lead_auth") == "test",
        can_manage_shift=lambda request, sid: sid == shift_id,
        resolve_my_shift=lambda request: db.get_shift(shift_id),
        shift_window=lambda st, day: (
            datetime.combine(day, time(5, 30)), datetime.combine(day, time(18, 0))
        ),
        lead_redirect=lambda dest: RedirectResponse("/v3/login", status_code=303),
        now_func=lambda: datetime(2026, 10, 25, 15, 0)
    ))
    import csv, io
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(HEADERS)
    writer.writerow(labor("28025869", "Carr, Alexander", "2026-10-25T08:30:00"))
    csv_bytes = buf.getvalue().encode("utf-8-sig")
    with TestClient(app) as client:
        client.cookies.set("lead_auth", "test")
        staged = client.post("/v3/import", data={"shift_id": str(shift_id)},
            files={"file": ("cetaris_export.csv", csv_bytes, "text/csv")})
        assert staged.status_code == 200, staged.text[:500]
        assert "1 unique WOs" in staged.text
        token = re.search(r"/v3/import/([0-9a-f]{32})/confirm", staged.text).group(1)
        confirm = client.post(f"/v3/import/{token}/confirm", data={
            "shift_id": str(shift_id), "techs": ["Carr, Alexander"],
        }, follow_redirects=False)
        assert confirm.status_code == 303, confirm.text[:500]
        assert {e["wo_number"] for e in db.get_entries(shift_id)} == {"28025869"}

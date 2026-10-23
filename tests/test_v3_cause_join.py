"""Regression + end-to-end coverage for the V3 cause/repair-description join.

Root cause this locks in: V2's cetaris_parser.py normalizes "WO Number" with
cause_lookup.normalize_wo() (strips the ".0" Excel adds to numeric cells) so
a separately-uploaded cause file joins correctly by WO Number. V3's
v3_cetaris.py did NOT do this, so a numeric WO Number produced "28017126.0"
in the timecard import while the cause file (same WO) produced "28017126" --
causes silently never appeared anywhere, with no error. See
tests/test_cause_wo_number_match.py for the original V2-side version of this
bug/test; this is the V3 equivalent plus a full upload-to-workspace check.
"""
import io
import re
from datetime import datetime, time, timedelta
from pathlib import Path

import openpyxl
import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import db
from v3_cetaris import parse_excel
from cause_lookup import parse_cause_file, normalize_wo
from v3_routes import create_v3_router

ROOT = Path(__file__).resolve().parents[1]


def _xlsx_bytes(headers: list[str], rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_v3_cetaris_wo_number_matches_cause_file_for_numeric_cells():
    timecard_xlsx = _xlsx_bytes(
        ["WO Number", "Asset #", "WO Status", "Primary RFR", "Service/Repair Description",
         "Technician", "Technician Remarks", "Start Date"],
        [[28017126, "TRKGATE0002", "Closed", "Breakdown", "Gate won't open",
          "Smith, John", "Replaced sensor", "10/20/2026 08:00 AM"]],
    )
    cause_xlsx = _xlsx_bytes(
        ["WO Number", "Repair Description"],
        [[28017126, "Sensor failure"]],
    )
    records = parse_excel(timecard_xlsx)
    assert len(records) == 1
    timecard_wo_number = records[0]["wo_number"]
    assert timecard_wo_number == normalize_wo(28017126)  # i.e. "28017126", no ".0"

    causes = parse_cause_file(cause_xlsx)
    assert timecard_wo_number in causes
    assert causes[timecard_wo_number] == "Sensor failure"


@pytest.fixture
def client_and_shift(tmp_path):
    db.set_db_path(tmp_path / "v3_cause_join.db")
    db.init_db()
    sid = db.start_shift("2026-10-20", "S2", "Test MST")
    app = FastAPI()
    app.include_router(create_v3_router(
        Jinja2Templates(directory=str(ROOT / "templates")),
        lambda request: request.cookies.get("lead_auth") == "test",
        lambda request, shift_id: True,
        lambda request: db.get_shift(sid),
        lambda shift, d: (datetime.combine(d, time(0)), datetime.combine(d + timedelta(days=1), time(0))),
        lambda dest: RedirectResponse("/lead/login", status_code=303),
    ))
    with TestClient(app) as client:
        client.cookies.set("lead_auth", "test")
        yield client, sid


def test_cause_file_upload_joins_into_workspace_happenings(client_and_shift):
    """Uploading the timecard file, then separately uploading the cause file,
    must surface the cause text in the workspace -- in either upload order."""
    client, sid = client_and_shift
    timecard_xlsx = _xlsx_bytes(
        ["WO Number", "Asset #", "WO Status", "Primary RFR", "Service/Repair Description",
         "Technician", "Technician Remarks", "Start Date"],
        [[28017126, "TRKGATE0002", "Closed", "Breakdown", "Gate won't open",
          "Smith, John", "Replaced sensor", "2026-10-20 08:00:00"]],
    )
    cause_xlsx = _xlsx_bytes(["WO Number", "Repair Description"], [[28017126, "Sensor failure"]])

    staged = client.post("/v3/import", data={"shift_id": sid},
                          files={"file": ("timecard.xlsx", timecard_xlsx,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert staged.status_code == 200
    token = re.search(r"/v3/import/([a-f0-9]{32})/confirm", staged.text).group(1)
    confirmed = client.post(f"/v3/import/{token}/confirm", data={"shift_id": sid, "techs": ["Smith, John"]},
                             follow_redirects=False)
    assert confirmed.status_code == 303

    # Cause file arrives AFTER the timecard -- a lead should never have to
    # worry about upload order.
    cause_upload = client.post("/v3/import", data={"shift_id": sid},
                                files={"file": ("cause.xlsx", cause_xlsx,
                                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert cause_upload.status_code == 200
    assert "Linked cause text for 1 work order" in cause_upload.text
    assert db.get_wo_causes(sid) == {"28017126": "Sensor failure"}

    workspace = client.get("/v3", params={"shift_id": sid})
    assert workspace.status_code == 200
    assert "Sensor failure" in workspace.text

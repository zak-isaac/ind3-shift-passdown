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
        ["WO Number", "Asset ID", "Asset #", "WO Status", "SJ / PM Code", "Repair Class",
         "Last Shift", "Service/Repair Description", "Technician", "Technician Remarks",
         "Cause", "Start Date", "WO Closed Date/Time", "Primary RFR"],
        [[28017126, None, "TRKGATE0002", "Closed", None, "Unplanned", "S2", "Gate won't open",
          "Smith, John", "Replaced sensor", None, "10/20/2026 08:00 AM", "10/20/2026 09:00 AM", "Breakdown"]],
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
        ["WO Number", "Asset ID", "Asset #", "WO Status", "SJ / PM Code", "Repair Class",
         "Last Shift", "Service/Repair Description", "Technician", "Technician Remarks",
         "Cause", "Start Date", "WO Closed Date/Time", "Primary RFR"],
        [[28017126, None, "TRKGATE0002", "Closed", None, "Unplanned", "S2", "Gate won't open",
          "Smith, John", "Replaced sensor", None, "2026-10-20 08:00:00", "2026-10-20 09:00:00", "Breakdown"]],
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


def test_main_import_cause_column_available_for_report_without_separate_file(client_and_shift):
    """Regression coverage for: an MST who only uploads the ONE main Cetaris
    timecard file (which has its own "Cause" column, no separate
    cause/repair-description export) saw cause text on the workspace page
    (served by v3_entry_meta.cause_text) but NOT in the emailed/downloaded
    report -- because report generation (main.py's lead_generate/shift_end)
    only ever read db.get_wo_causes(), which is exclusively populated by the
    SEPARATE cause-file upload path and knows nothing about the main
    import's own Cause column. db.get_v3_entry_causes() is the fix: it reads
    the same per-entry cause_text the workspace page already uses, so report
    generation can merge it in too.
    """
    client, sid = client_and_shift
    timecard_xlsx = _xlsx_bytes(
        ["WO Number", "Asset ID", "Asset #", "WO Status", "SJ / PM Code", "Repair Class",
         "Last Shift", "Service/Repair Description", "Technician", "Technician Remarks",
         "Cause", "Start Date", "WO Closed Date/Time", "Primary RFR"],
        [[28099999, None, "TRKGATE0003", "Closed", None, "Unplanned", "S2", "Gate won't open",
          "Smith, John", "Replaced sensor", "Sensor failure (from main file)",
          "2026-10-20 08:00:00", "2026-10-20 09:00:00", "Breakdown"]],
    )
    staged = client.post("/v3/import", data={"shift_id": sid},
                          files={"file": ("timecard.xlsx", timecard_xlsx,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    token = re.search(r"/v3/import/([a-f0-9]{32})/confirm", staged.text).group(1)
    confirmed = client.post(f"/v3/import/{token}/confirm", data={"shift_id": sid, "techs": ["Smith, John"]},
                             follow_redirects=False)
    assert confirmed.status_code == 303

    # No separate cause file uploaded at all -- the dedicated-file lookup
    # must stay empty, while the main-import lookup must carry the cause.
    assert db.get_wo_causes(sid) == {}
    assert db.get_v3_entry_causes(sid) == {"28099999": "Sensor failure (from main file)"}

    # Same merge pattern used in main.py's report generation call sites.
    merged = {**db.get_wo_causes(sid), **db.get_v3_entry_causes(sid)}
    assert merged["28099999"] == "Sensor failure (from main file)"


def test_empty_cause_column_falls_back_to_service_description(client_and_shift):
    """Real-world case at this facility: the literal 'Cause' column is
    empty almost throughout; 'Service/Repair Description' is where the
    actual cause narrative lives in practice. get_v3_entry_causes() must
    fall back to it -- matching the EXACT fallback the workspace page
    already uses (v3_routes.py's model(): cause_text or reported_issue) --
    or the email report shows no cause for the vast majority of real WOs
    even after the Cause-column fix.
    """
    client, sid = client_and_shift
    timecard_xlsx = _xlsx_bytes(
        ["WO Number", "Asset ID", "Asset #", "WO Status", "SJ / PM Code", "Repair Class",
         "Last Shift", "Service/Repair Description", "Technician", "Technician Remarks",
         "Cause", "Start Date", "WO Closed Date/Time", "Primary RFR"],
        [[28088888, None, "TRKGATE0004", "Closed", None, "Unplanned", "S2",
          "Conveyor belt jammed with debris", "Smith, John", "Cleared jam, inspected belt",
          None,  # Cause column left blank, as in real exports
          "2026-10-20 08:00:00", "2026-10-20 09:00:00", "Breakdown"]],
    )
    staged = client.post("/v3/import", data={"shift_id": sid},
                          files={"file": ("timecard.xlsx", timecard_xlsx,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    token = re.search(r"/v3/import/([a-f0-9]{32})/confirm", staged.text).group(1)
    confirmed = client.post(f"/v3/import/{token}/confirm", data={"shift_id": sid, "techs": ["Smith, John"]},
                             follow_redirects=False)
    assert confirmed.status_code == 303

    assert db.get_v3_entry_causes(sid) == {"28088888": "Conveyor belt jammed with debris"}

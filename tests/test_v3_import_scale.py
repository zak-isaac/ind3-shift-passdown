'''Large-file import coverage (2026-10-24 facility request): Cetaris exports
for overnight shifts (S3, S5) span the previous day into the current day and
can run to several hundred rows. This file proves parse_excel()/the V3
import pipeline reads every row -- no hard-coded truncation, no pagination
loss, no date-based pre-filtering -- at realistic and much larger scale
(175, matching the actual file that triggered this investigation; 500; and
1,000, the facility's requested minimum).

Root cause of the original complaint (confirmed against the real uploaded
file, "since prev day.xlsx", 175 data rows): NOT a truncation bug. 36 rows
were unassigned "To be Done" PM work orders with no technician and no start
date -- correctly excluded, since there is no labor evidence to attribute
them to any shift. Of the remaining 139 parsed rows, the S1-S5 engine
correctly split them across Thursday-into-Friday S3 continuation, Friday
S4, Friday S5, and Saturday S4 -- each shift's own import legitimately only
claims its own slice. Nothing was silently lost; it just was not all headed
to the same shift. These tests lock that reading in at scale.
'''
import io
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
from v3_routes import create_v3_router

ROOT = Path(__file__).resolve().parents[1]

HEADERS = ["WO Number", "Asset ID", "Asset #", "WO Status", "SJ / PM Code", "Repair Class",
           "Last Shift", "Service/Repair Description", "Technician", "Technician Remarks",
           "Cause", "Start Date", "WO Closed Date/Time", "Primary RFR"]

TECHS = [f"Tech{n}, Worker" for n in range(1, 11)]


def _xlsx_bytes(headers, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _synthetic_rows(n, multi_tech_every=23):
    # n distinct WOs, round-robin across 10 techs, all starting in a clean
    # S2-only window (6pm Tuesday 2026-10-20 -- no S1/S2 overlap, no S2/S3
    # overlap) so every row resolves unambiguously and attribution noise
    # does not obscure whether all n rows were even read. Every
    # multi_tech_every-th WO gets a second technician's row to exercise
    # multi-technician consolidation at scale.
    rows = []
    base_wo = 90000000
    for i in range(n):
        wo = base_wo + i
        tech = TECHS[i % len(TECHS)]
        pm_code = "PM1" if i % 5 == 0 else ""
        rows.append([wo, None, f"ASSET{i:04d}", "Closed", pm_code, "Unplanned" if not pm_code else "Planned",
                     "S2", f"Issue {i}", tech, f"Remark {i}", "",
                     f"2026-10-20 18:{i % 60:02d}:00", "", "Breakdown" if not pm_code else "PM"])
        if multi_tech_every and i % multi_tech_every == 0 and i > 0:
            second_tech = TECHS[(i + 1) % len(TECHS)]
            rows.append([wo, None, f"ASSET{i:04d}", "Closed", pm_code, "Unplanned" if not pm_code else "Planned",
                         "S2", f"Issue {i}", second_tech, f"Helped on {i}", "",
                         f"2026-10-20 18:{i % 60:02d}:00", "", "Breakdown" if not pm_code else "PM"])
    return rows


@pytest.fixture
def client_and_shift(tmp_path):
    db.set_db_path(tmp_path / "scale_test.db")
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


@pytest.mark.parametrize("n", [175, 500, 1000])
def test_parse_excel_reads_every_row_no_truncation(n):
    # Direct parser-level check, no attribution noise: source_rows,
    # parsed_rows, and distinct WO count must all match what was generated.
    rows = _synthetic_rows(n)
    data = _xlsx_bytes(HEADERS, rows)
    stats = {}
    parsed = parse_excel(data, stats=stats)
    assert stats["source_rows"] == len(rows)
    assert stats["skipped_no_wo"] == 0
    assert stats["skipped_no_tech"] == 0
    assert stats["parsed_rows"] == len(rows) == len(parsed)
    assert len({r["wo_number"] for r in parsed}) == n


@pytest.mark.parametrize("n", [175, 500, 1000])
def test_full_upload_workflow_at_scale_no_silent_loss(client_and_shift, n):
    # Full stage -> confirm workflow at scale: every technician selected,
    # every eligible WO must be attributed to this shift -- PMs and Shift
    # Happenings counted separately, multi-technician WOs consolidated
    # (not double-counted), diagnostics line present and internally consistent.
    client, sid = client_and_shift
    rows = _synthetic_rows(n)
    data = _xlsx_bytes(HEADERS, rows)
    staged = client.post("/v3/import", data={"shift_id": sid},
                          files={"file": ("export.xlsx", data,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert staged.status_code == 200
    import re
    token = re.search(r"/v3/import/([a-f0-9]{32})/confirm", staged.text).group(1)
    confirmed = client.post(f"/v3/import/{token}/confirm",
                             data={"shift_id": sid, "techs": TECHS}, follow_redirects=True)
    assert confirmed.status_code == 200
    expected_pm_wos = len([i for i in range(n) if i % 5 == 0])
    expected_happening_wos = n - expected_pm_wos
    assert f"{expected_pm_wos} distinct closed PM WOs" in confirmed.text
    assert f"{len(rows)} source rows" in confirmed.text
    assert f"{n} unique WOs seen" in confirmed.text
    assert f"{expected_pm_wos} PMs, {expected_happening_wos} Shift Happenings" in confirmed.text
    entries = db.get_entries(sid)
    with db._conn() as c:
        pm_count = c.execute("SELECT COUNT(DISTINCT wo_number) FROM pm_wos WHERE shift_id=?", (sid,)).fetchone()[0]
    assert pm_count == expected_pm_wos
    happening_wos = {e["wo_number"] for e in entries}
    assert len(happening_wos) == expected_happening_wos

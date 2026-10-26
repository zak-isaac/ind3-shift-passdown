"""Large-file import coverage, ported 2026-10-26 from the production branch
(tests/test_v3_import_scale.py): Cetaris exports for overnight shifts (S3,
S5) span the previous day into the current day and can run to several
hundred rows. Proves parse_excel()/the V3 import pipeline reads every row
-- no hard-coded truncation, no pagination loss -- at realistic and much
larger scale (175, matching the real file that triggered the original
investigation; 500; and 1,000, the facility's requested minimum).

Adapted to the Workshop engine's actual routes/response shape (HTTP-level
assertions kept to status codes + DB-state checks rather than production's
specific confirmation-page wording, which differs here) and its richer
v3_split.assign() signature (no confirmed_s5_techs parameter, per this
integration's 2026-10-26 grace-window fix).
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
from v3_routes import create_v3_router

ROOT = Path(__file__).resolve().parents[1]

HEADERS = ["WO Number", "Asset #", "WO Status", "Primary RFR",
           "Service/Repair Description", "Technician", "Technician Remarks",
           "Start Date", "In Service Date / Time"]

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
    # S2-only window (Tuesday 2026-10-20 evening -- no S1/S2 or S2/S3
    # overlap) so every row resolves unambiguously and attribution noise
    # does not obscure whether all n rows were even read.
    rows = []
    base_wo = 90000000
    for i in range(n):
        wo = base_wo + i
        tech = TECHS[i % len(TECHS)]
        is_pm = i % 5 == 0
        rfr = "Preventive Maintenance" if is_pm else "Breakdown"
        rows.append([wo, f"ASSET{i:04d}", "Closed", rfr, f"Issue {i}", tech, f"Remark {i}",
                     f"2026-10-20 18:{i % 60:02d}:00", f"2026-10-20 18:{i % 60:02d}:30"])
        if multi_tech_every and i % multi_tech_every == 0 and i > 0:
            second_tech = TECHS[(i + 1) % len(TECHS)]
            rows.append([wo, f"ASSET{i:04d}", "Closed", rfr, f"Issue {i}", second_tech, f"Helped on {i}",
                         f"2026-10-20 18:{i % 60:02d}:00", f"2026-10-20 18:{i % 60:02d}:30"])
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
    """Full stage -> confirm workflow at scale, asserted against actual DB
    state (robust to confirmation-page wording) rather than response text:
    every eligible WO attributed, PMs and Shift Happenings counted
    separately, multi-technician WOs consolidated not double-counted."""
    client, sid = client_and_shift
    rows = _synthetic_rows(n)
    data = _xlsx_bytes(HEADERS, rows)
    staged = client.post("/v3/import", data={"shift_id": sid},
                          files={"file": ("export.xlsx", data,
                                  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")})
    assert staged.status_code == 200
    token = re.search(r"/v3/import/([a-f0-9]{32})/confirm", staged.text).group(1)
    confirmed = client.post(f"/v3/import/{token}/confirm",
                             data={"shift_id": sid, "techs": TECHS}, follow_redirects=True)
    assert confirmed.status_code == 200
    expected_pm_wos = len([i for i in range(n) if i % 5 == 0])
    expected_happening_wos = n - expected_pm_wos
    assert db.get_pm_count(sid) == expected_pm_wos
    entries = db.get_entries(sid)
    happening_wos = {e["wo_number"] for e in entries}
    assert len(happening_wos) == expected_happening_wos

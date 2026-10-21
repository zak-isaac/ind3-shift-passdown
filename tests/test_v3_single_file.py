"""V3 parser regression tests using the sample Cetaris trial.xlsx export.

pytest -q tests/test_v3_single_file.py (set CETARIS_SAMPLE to local file path)
"""
import os
from datetime import datetime
from pathlib import Path

import pytest
from v3_cetaris import parse_excel, select_records, technician_counts

SAMPLE = Path(os.getenv("CETARIS_SAMPLE", "trial.xlsx"))


def test_real_trial_selected_technicians():
    if not SAMPLE.exists():
        pytest.skip("Trial workbook not present in deployment bundle")
    rows = parse_excel(SAMPLE.read_bytes())
    assert len(rows) == 72
    counts = {r["name"]: r["count"] for r in technician_counts(rows)}
    assert counts["Ssaku, Isaac"] == 3
    assert counts["Melvin, Douglas"] == 4
    selected = {"Ssaku, Isaac", "Melvin, Douglas", "Bawi, Mang", "Popoola, Waheed"}
    output, stats = select_records(rows, selected, datetime(2026,10,20,0),datetime(2026,10,21,0))
    assert len(output) == 12
    assert len({r["wo_number"] for r in output if r["kind"] == "pm"}) == 8
    assert all(r["tech_name"] in selected for r in output)


def test_multi_labor_rows_and_open_wo_are_not_miscounted():
    stub = dict(wo_number="100",tech_name="Tech A",asset_id="M1",status="Closed",rfr="Breakdown",reported_issue="Stop",cause="",remarks="First",start_at="2026-10-20T13:00:00")
    rows = [stub, {**stub,"remarks":"Second"},{**stub,"wo_number":"101","status":"Open","remarks":"not done"}]
    got,summary=select_records(rows,{"Tech A"},datetime(2026,10,20,12),datetime(2026,10,20,23))
    assert len(got)==1
    assert got[0]["remarks_list"]==["First","Second"]
    assert summary["not_closed"]==1

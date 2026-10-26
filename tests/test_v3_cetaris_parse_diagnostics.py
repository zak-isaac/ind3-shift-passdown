"""parse_excel() import-diagnostics stats (ported 2026-10-26 from the
production branch): an MST should be able to SEE why a smaller-than-
expected row count came through an import, not just infer it from a
suspicious total. Purely additive -- stats is optional and defaults to {}.
"""
from io import BytesIO
from openpyxl import Workbook
from v3_cetaris import parse_excel

HEADERS = ("WO Number", "Asset #", "WO Status", "Primary RFR",
           "Service/Repair Description", "Technician", "Technician Remarks")


def _workbook(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(list(HEADERS))
    for row in rows:
        ws.append(row)
    buf = BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_stats_counts_source_skipped_and_parsed_rows():
    data = _workbook([
        ("101", "A1", "Closed", "Breakdown", "Broke", "Tech A", "Fixed it"),
        ("", "A2", "Open", "Breakdown", "No WO number", "Tech A", ""),  # skipped_no_wo
        ("103", "A3", "Open", "", "No technician yet", "", ""),         # skipped_no_tech
    ])
    stats = {}
    records = parse_excel(data, stats=stats)
    assert len(records) == 1
    assert stats == {
        "source_rows": 3, "skipped_no_wo": 1,
        "skipped_no_tech": 1, "parsed_rows": 1,
    }


def test_stats_defaults_to_empty_dict_when_not_requested():
    data = _workbook([("101", "A1", "Closed", "Breakdown", "Broke", "Tech A", "Fixed it")])
    records = parse_excel(data)  # no stats kwarg -- existing callers unaffected
    assert len(records) == 1


def test_optional_provenance_columns_default_blank_when_absent():
    """pm_code/repair_class/updated_at/completed_by/last_technician are not
    in this facility's REQUIRED column set -- absence must never block an
    import, each just defaults to an empty string."""
    data = _workbook([("101", "A1", "Closed", "Breakdown", "Broke", "Tech A", "Fixed it")])
    records = parse_excel(data)
    row = records[0]
    assert row["pm_code"] == "" and row["repair_class"] == ""
    assert row["updated_at"] == "" and row["completed_by"] == ""
    assert row["last_technician"] == ""

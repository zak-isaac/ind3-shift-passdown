"""Regression test: cetaris_parser.py and cause_lookup.py must normalize
WO Number the exact same way.

Root cause this locks in: Excel stores a numeric "WO Number" column as a
float cell (e.g. 28017126.0), not text. cause_lookup.py's normalize_wo()
already strips the trailing ".0" when reading the cause export. Before this
fix, cetaris_parser.py did NOT do the same thing for the timecard export,
so an .xlsx timecard upload produced a happening keyed by the WO number
"28017126.0" while the cause file (same WO, same shift) produced the key
"28017126". consolidated_to_happenings() -> cause_lookup.get(wo.wo_number)
then silently missed every match: the report rendered exactly as if no
cause file had ever been uploaded, with no error or warning anywhere.

This test builds a real .xlsx for each file type (not just CSV, since CSV
text columns don't reproduce the float-cell bug) and asserts the WO Number
keys produced by both parsers agree.
"""
import io
import sys
import unittest
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import openpyxl

from cetaris_parser import parse_cetaris
from cause_lookup import parse_cause_file, normalize_wo


def _xlsx_bytes(headers: list[str], rows: list[list]) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(headers)
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


class TestCauseWoNumberMatch(unittest.TestCase):
    def test_xlsx_numeric_wo_number_matches_cause_file(self):
        # WO Number written as a real number (28017126), the way Excel/Cetaris
        # exports it -- openpyxl will store this as a numeric cell, so
        # reading it back yields a float (28017126.0), reproducing the bug.
        timecard_xlsx = _xlsx_bytes(
            ["Asset #", "WO Number", "Status", "Work Accomplished", "Tech Remarks"],
            [["TRKGATE0002", 28017126, "Completed", "Repair", "Replaced sensor"]],
        )
        cause_xlsx = _xlsx_bytes(
            ["WO Number", "Repair Description"],
            [[28017126, "Sensor failure"]],
        )

        parsed = parse_cetaris(timecard_xlsx)
        self.assertEqual(len(parsed.work_orders), 1)
        timecard_wo_number = parsed.work_orders[0].wo_number

        causes = parse_cause_file(cause_xlsx)
        self.assertIn(timecard_wo_number, causes)
        self.assertEqual(causes[timecard_wo_number], "Sensor failure")

    def test_normalize_wo_strips_trailing_dot_zero(self):
        self.assertEqual(normalize_wo(28017126.0), "28017126")
        self.assertEqual(normalize_wo("28017126.0"), "28017126")
        self.assertEqual(normalize_wo("28017126"), "28017126")
        self.assertEqual(normalize_wo(None), "")

    def test_real_cetaris_desktop_export_never_misdetected_as_cause_file(self):
        """Root cause of a live outage: the real Cetaris Desktop timecard export
        has BOTH 'Primary RFR' and a separate plain 'RFR' column, plus
        'Technician Remarks' (not 'Tech Remarks'). looks_like_cause_file() only
        guarded against the literal header 'Tech Remarks', so a genuine 100+
        column timecard export -- which also satisfies 'WO Number' + 'RFR' --
        got misrouted through the cause-only path: entire upload silently
        swallowed, zero entries created, no technician-selection popup, no
        error shown anywhere."""
        from cause_lookup import looks_like_cause_file
        real_shaped_timecard = _xlsx_bytes(
            ["WO Number", "Asset #", "WO Status", "Primary RFR", "RFR",
             "Service/Repair Description", "Technician", "Technician Remarks", "Start Date"],
            [[28017126, "TRKGATE0002", "Closed", "Proactive Repair", "Proactive Repair",
              "Gate check", "Smith, John", "Checked alignment", "10/22/2026 3:00 PM"]],
        )
        self.assertFalse(looks_like_cause_file(real_shaped_timecard))


if __name__ == "__main__":
    unittest.main()

"""Regression coverage for the "AssetID : Cause : Remark (WO#)" email format.

Real bug found 2026-10-23: the first attempt at surfacing cause text in the
emailed report embedded it directly into `title` (e.g. "VOIDFILL0901 :
Tape "). That corrupted the pipeline in two ways:

1. ai_rewriter.build_input(asset_id, remarks) formats its AI input as
   "ASSET_ID / remark1 | remark2" -- main.py passes `title` straight in as
   `asset_id`, so the cause text ended up baked into that string and came
   out the other end as "VOIDFILL0901 : Tape / Found two photos...".
2. main.py's asset_groups merge step keys directly off `title` to group
   multiple WOs for the same physical asset -- a cause-polluted title would
   have broken that grouping too.

`title` must always stay a bare asset ID. Cause is a separate field,
rendered by the formatters as its own "title : cause : paragraph" segment.
"""
import unittest

import ai_rewriter
from report_models import ReportData, RewrittenHappening
from formatter import format_email_passdown_html, format_html_preview
from wo_consolidator import consolidate_entries, consolidated_to_happenings


def _entries():
    return [
        {
            "id": 1, "shift_id": 1, "tech_name": "Smith, John",
            "unit": "VOIDFILL0901", "wo_number": "28025822",
            "issue": "Tape", "raw_remarks": "Found two photos out of alignment, reset, tested.",
            "priority": "monitor", "created_at": "2026-10-23 08:00:00", "excluded": 0,
        },
    ]


class TestTitleStaysCleanAssetId(unittest.TestCase):
    def test_consolidated_to_happenings_keeps_title_bare(self):
        """cause must be its own key, never folded into title."""
        result = consolidate_entries(_entries())
        happenings = consolidated_to_happenings(result, cause_lookup={"28025822": "Tape"})
        self.assertEqual(len(happenings), 1)
        h = happenings[0]
        self.assertEqual(h["title"], "VOIDFILL0901")
        self.assertEqual(h["cause"], "Tape")

    def test_ai_rewriter_input_not_corrupted_by_cause(self):
        """build_input() must receive a bare asset ID -- simulates what
        main.py's lead_generate does with the resolved title."""
        result = consolidate_entries(_entries())
        happenings = consolidated_to_happenings(result, cause_lookup={"28025822": "Tape"})
        title = happenings[0]["title"]  # what main.py passes as asset_id
        combined = ai_rewriter.build_input(title, ["Found two photos out of alignment, reset, tested."])
        self.assertEqual(
            combined,
            "VOIDFILL0901 / Found two photos out of alignment, reset, tested.",
        )
        self.assertNotIn("Tape", combined)


class TestCauseRenderingInEmailOutput(unittest.TestCase):
    def _happening(self, cause="Tape"):
        return RewrittenHappening(
            title="VOIDFILL0901", cause=cause,
            paragraph="Found two photos out of alignment, reset, tested. Returned to service.",
            wo_number="28025822", tech_name="Smith, John",
        )

    def test_html_email_shows_asset_cause_remark_wo(self):
        data = ReportData(date="2026-10-23", shift_type="S4", lead_name="Isaac S",
                           happenings=[self._happening()])
        html = format_email_passdown_html(data)
        self.assertIn("VOIDFILL0901", html)
        self.assertIn(": Tape : Found two photos", html)
        self.assertIn("WO# 28025822", html)
        # The old broken format joined cause and remark with " / " instead
        # of a clean " : " separator -- must never reappear.
        self.assertNotIn("Tape / Found", html)

    def test_html_preview_shows_asset_cause_remark_wo(self):
        data = ReportData(date="2026-10-23", shift_type="S4", lead_name="Isaac S",
                           happenings=[self._happening()])
        html = format_html_preview(data)
        self.assertIn(": Tape : Found two photos", html)
        self.assertNotIn("Tape / Found", html)

    def test_no_cause_falls_back_to_plain_asset_colon_remark(self):
        data = ReportData(date="2026-10-23", shift_type="S4", lead_name="Isaac S",
                           happenings=[self._happening(cause="")])
        html = format_email_passdown_html(data)
        self.assertIn("VOIDFILL0901</strong> : Found two photos", html)


if __name__ == "__main__":
    unittest.main()

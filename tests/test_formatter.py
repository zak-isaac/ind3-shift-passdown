"""Tests for formatter.py -- MST Notes incorporated into Shift Happenings.

Real bug found 2026-09-16: shift_notes was captured on the lead dashboard
and stored in the DB, and even showed up on the shift_ended.html and
print_report.html pages -- but ReportData (the model that feeds every
actual generated report/email) never carried the field at all, so the
notes silently vanished from every report the MST actually sends out.

Design note: Isaac asked for notes to be incorporated directly into the
Shift Happenings section (as its own leading bullet/paragraph), not shown
as a separate "MST Notes" section -- so these tests check the note text
lands inside the Shift Happenings block, not a standalone heading.
"""
import unittest

from report_models import ReportData
from formatter import (
    format_html_preview,
    format_email,
    format_email_passdown,
    format_email_passdown_html,
)


def _base_data(shift_notes: str = "") -> ReportData:
    return ReportData(
        date="2026-09-16",
        shift_type="S2",
        lead_name="Isaac S",
        shift_notes=shift_notes,
    )


class TestShiftNotesInShiftHappenings(unittest.TestCase):
    NOTE = "Received a call regarding third-floor SLAM areas being impacted."

    def _assert_note_within_happenings(self, text: str, marker_before: str, marker_after: str) -> None:
        """The note must appear between the Shift Happenings heading and
        whatever section comes right after it -- proving it's inside that
        section, not off in a separate one."""
        start = text.index(marker_before)
        end = text.index(marker_after, start)
        self.assertIn(self.NOTE, text[start:end])

    def test_html_preview_note_inside_shift_happenings(self):
        html = format_html_preview(_base_data(self.NOTE))
        self._assert_note_within_happenings(html, "Shift Happenings", "Reliable Operations")

    def test_html_preview_no_note_block_when_blank(self):
        html = format_html_preview(_base_data(""))
        self.assertNotIn(self.NOTE, html)

    def test_plain_text_email_note_inside_shift_happenings(self):
        text = format_email(_base_data(self.NOTE))
        self._assert_note_within_happenings(text, "SHIFT HAPPENINGS", "RELIABLE OPERATIONS")

    def test_passdown_bullet_email_note_inside_shift_happenings(self):
        text = format_email_passdown(_base_data(self.NOTE))
        self._assert_note_within_happenings(text, "Shift Happenings", "Reliable Operations")

    def test_outlook_html_email_note_inside_shift_happenings(self):
        html = format_email_passdown_html(_base_data(self.NOTE))
        self._assert_note_within_happenings(html, "Shift Happenings", "Reliable Operations")

    def test_outlook_html_email_no_note_when_blank(self):
        html = format_email_passdown_html(_base_data(""))
        self.assertNotIn(self.NOTE, html)

    def test_whitespace_only_notes_treated_as_blank(self):
        html = format_email_passdown_html(_base_data("   \n  "))
        self.assertNotIn(self.NOTE, html)


if __name__ == "__main__":
    unittest.main()

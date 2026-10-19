"""Regression tests for wo_consolidator.py, especially has_real_remarks().

This module had ZERO test coverage before this file, which is exactly how
a serious hallucination bug shipped to production undetected on 2026-09-17:

A technician logged a CMMS work order (TRKGATE0002, WO#27815641) but never
typed any remark text -- `raw_remarks` stayed an empty list. _build_wo()
correctly builds an honest placeholder summary for display purposes:
"WO #27815641 worked on by Isaac S. No remarks recorded." But that
placeholder is OUR OWN synthetic text, not something the technician wrote,
and it happens to be 10 words -- long enough to sail past
ai_rewriter._MIN_WORDS_FOR_AI (8) when main.py's /lead/generate handler
naively used it as AI input whenever a WO had no real remarks. The model
then fabricated an entire narrative to fill the gap: "The gate at location
002 was serviced under work order number 27815641, however, no further
observations were noted following our engineer Isaac S.'s intervention."
-- inventing a resolution that never happened and dragging the tech's real
name into invented prose.

Fix: has_real_remarks() is the single source of truth callers must check
before ever handing WO content to the AI rewriter. These tests lock in
that contract at the wo_consolidator level (data-shape correctness) so it
can't regress silently again.
"""
import sys
import unittest
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from wo_consolidator import consolidate_entries, has_real_remarks, ConsolidatedWO


def _entry(**overrides) -> dict:
    base = {
        "id": 1,
        "shift_id": 74,
        "tech_name": "Isaac S",
        "unit": "TRKGATE0002",
        "issue": "",
        "action": "",
        "result": "",
        "created_at": "2026-09-17 22:26:02",
        "priority": "monitor",
        "carry_over": 0,
        "source": "cmms",
        "wo_number": "27815641",
        "raw_remarks": "[]",
        "excluded": 0,
    }
    base.update(overrides)
    return base


class TestHasRealRemarks(unittest.TestCase):
    def test_false_when_all_remarks_empty(self):
        cwo = ConsolidatedWO(
            wo_number="27815641", asset_id="TRKGATE0002", techs=["Isaac S"],
            all_remarks=[], tech_remarks=[],
            summary="WO #27815641 worked on by Isaac S. No remarks recorded.",
            priority="monitor", first_logged="2026-09-17 22:26:02",
            last_logged="2026-09-17 22:26:02", raw_entry_ids=[361], entry_count=1,
        )
        self.assertFalse(has_real_remarks(cwo))

    def test_true_when_remarks_present(self):
        cwo = ConsolidatedWO(
            wo_number="27974286", asset_id="BALER0004", techs=["Isaac S"],
            all_remarks=["Baler binding wire replaced"], tech_remarks=[("Isaac S", "Baler binding wire replaced")],
            summary="Baler binding wire replaced.",
            priority="monitor", first_logged="2026-09-17 22:02:33",
            last_logged="2026-09-17 22:02:33", raw_entry_ids=[358], entry_count=1,
        )
        self.assertTrue(has_real_remarks(cwo))


class TestNoRemarksWOEndToEnd(unittest.TestCase):
    """Reproduces the exact production incident: a CMMS entry with an empty
    raw_remarks list and a WO-number-only issue string (the prefix strips
    to nothing) must consolidate down to a WO that has_real_remarks()
    correctly flags as having no real content."""

    def test_empty_raw_remarks_and_prefix_only_issue_yields_no_real_remarks(self):
        entries = [_entry(issue="WO #27815641 \u2014 ", raw_remarks="[]")]
        result = consolidate_entries(entries)
        self.assertEqual(len(result.consolidated), 1)
        cwo = result.consolidated[0]
        self.assertEqual(cwo.all_remarks, [])
        self.assertFalse(has_real_remarks(cwo))
        # The placeholder summary must still be honest and must still
        # contain enough words to have slipped past a naive word-count
        # gate -- that's WHY this needs an explicit check, not implicit.
        self.assertIn("No remarks recorded", cwo.summary)
        self.assertGreater(len(cwo.summary.split()), 8)

    def test_real_remark_yields_has_real_remarks_true(self):
        entries = [_entry(
            unit="BALER0004", wo_number="27974286",
            issue="WO #27974286 \u2014 Baler binding wire replaced",
            raw_remarks='["Baler binding wire replaced"]',
        )]
        result = consolidate_entries(entries)
        cwo = result.consolidated[0]
        self.assertTrue(has_real_remarks(cwo))
        self.assertNotIn("No remarks recorded", cwo.summary)


if __name__ == "__main__":
    unittest.main()

import json
import unittest
from datetime import datetime

import wo_consolidator as wc


class TestPossibleDuplicateDetection(unittest.TestCase):
    """Purely time-based signal (see wo_consolidator.py module comment for
    why text-similarity was tried and rejected: it flagged genuinely
    different work -- 'left side' vs 'right side' -- as duplicates while
    missing an actual near-duplicate, because a single word swap that
    changes meaning scores just as 'similar' as a restatement)."""

    def test_two_remarks_within_window_flagged(self):
        times = [datetime(2026, 9, 19, 9, 0, 0), datetime(2026, 9, 19, 9, 5, 0)]
        self.assertTrue(wc._detect_possible_duplicates(times))

    def test_two_remarks_far_apart_not_flagged(self):
        times = [datetime(2026, 9, 19, 9, 0, 0), datetime(2026, 9, 19, 14, 0, 0)]
        self.assertFalse(wc._detect_possible_duplicates(times))

    def test_single_remark_never_flagged(self):
        self.assertFalse(wc._detect_possible_duplicates([datetime(2026, 9, 19, 9, 0, 0)]))

    def test_empty_list_never_flagged(self):
        self.assertFalse(wc._detect_possible_duplicates([]))

    def test_exactly_at_window_boundary_flagged(self):
        times = [datetime(2026, 9, 19, 9, 0, 0), datetime(2026, 9, 19, 9, 15, 0)]
        self.assertTrue(wc._detect_possible_duplicates(times))


class TestPossibleDuplicateEndToEnd(unittest.TestCase):
    """Same tech, same WO, near-simultaneous check-ins with similar but
    not identical wording -- the exact scenario Isaac asked about."""

    def _entry(self, remark: str, created_at: str, tech: str = "Isaac S", wo: str = "27974144", unit: str = "VOIDFILL0801"):
        return {
            "id": abs(hash((remark, created_at))) % 100000,
            "unit": unit,
            "wo_number": wo,
            "tech_name": tech,
            "created_at": created_at,
            "priority": "monitor",
            "excluded": False,
            "source": "manual",
            "issue": "",
            "raw_remarks": json.dumps([remark]),
        }

    def test_flagged_and_both_remarks_preserved(self):
        entries = [
            self._entry("Checked belt tension, ok", "2026-09-19 09:00:00"),
            self._entry("Checked the belt tension, it was ok", "2026-09-19 09:02:00"),
        ]
        result = wc.consolidate_entries(entries)
        cwo = result.consolidated[0]
        self.assertTrue(cwo.possible_duplicate)
        # Both survive verbatim -- flag, don't merge or drop.
        self.assertEqual(len(cwo.all_remarks), 2)

        happenings = wc.consolidated_to_happenings(result)
        self.assertIn("Possible duplicate remarks", happenings[0]["paragraph"])
        self.assertTrue(happenings[0]["possible_duplicate"])

    def test_not_flagged_when_hours_apart(self):
        entries = [
            self._entry("Checked belt tension, ok", "2026-09-19 09:00:00"),
            self._entry("Found belt worn, replaced it", "2026-09-19 15:00:00"),
        ]
        result = wc.consolidate_entries(entries)
        cwo = result.consolidated[0]
        self.assertFalse(cwo.possible_duplicate)


if __name__ == "__main__":
    unittest.main()

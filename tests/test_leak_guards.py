"""Tests for two guards added 2026-09-16 after batch-testing real remarks:

1. _strip_ride_on_vehicle_leak -- the prompt injects "This asset is a
   ride-on vehicle." as background context for confirmed-vehicle assets
   (TSP/RC/CR/PERCART) with an explicit "never repeat this sentence"
   instruction. phi4-mini ignores it and weaves the literal phrase into
   its own sentence. Real examples: CR0003 -> "Unlocked this ride-on
   vehicle..."; TSP0002 -> "...for this ride-on vehicle...".

2. _leaked_asset_id_digits -- a no-separator asset ID (VOIDFILL0801) can
   get misread by the model as English words + a number instead of an
   opaque identifier. Real example: VOIDFILL0801 -> "Has voided fill 0801
   on this machine...".
"""
import unittest

import ai_rewriter as ar


class TestRideOnVehicleLeak(unittest.TestCase):
    def test_strips_leaked_phrase(self):
        out = ar._strip_ride_on_vehicle_leak(
            "Unlocked this ride-on vehicle after inspecting it closely.",
            original="Unlocked the equipment and checked for any physical damage.",
        )
        self.assertNotIn("ride-on vehicle", out.lower())
        self.assertIn("the asset", out.lower())

    def test_mid_sentence_leak_also_stripped(self):
        out = ar._strip_ride_on_vehicle_leak(
            "After retrieving components for this ride-on vehicle, proceeded with installing screws.",
            original="I got the gasket from TSP0008 and installed the stud screws.",
        )
        self.assertNotIn("ride-on vehicle", out.lower())

    def test_leaves_output_alone_if_technician_actually_said_it(self):
        out = ar._strip_ride_on_vehicle_leak(
            "The ride-on vehicle was inspected as usual.",
            original="Checked the ride-on vehicle for damage as usual.",
        )
        self.assertIn("ride-on vehicle", out.lower())

    def test_no_double_the_after_strip(self):
        out = ar._strip_ride_on_vehicle_leak(
            "Inspected the ride-on vehicle for damage.",
            original="Checked it for damage.",
        )
        self.assertNotIn("the the asset", out.lower())


class TestLeakedAssetIdDigits(unittest.TestCase):
    def test_detects_voidfill_style_leak(self):
        leaked = ar._leaked_asset_id_digits(
            "Has voided fill 0801 on this machine by taping over a seal.",
            asset_id="VOIDFILL0801",
            original_body="Taped changed and spacer added to control the spring tension.",
        )
        self.assertTrue(leaked)

    def test_no_false_positive_when_number_legitimately_in_remark(self):
        # Technician's own remark mentions the number -- not a leak.
        leaked = ar._leaked_asset_id_digits(
            "Moved the part to location 0801 as instructed.",
            asset_id="VOIDFILL0801",
            original_body="Moved the part to location 0801 as instructed.",
        )
        self.assertFalse(leaked)

    def test_no_false_positive_when_asset_id_has_no_digit_suffix(self):
        leaked = ar._leaked_asset_id_digits(
            "Checked the asset for damage.",
            asset_id="BLDG",
            original_body="Checked the building for damage.",
        )
        self.assertFalse(leaked)

    def test_no_false_positive_clean_output(self):
        leaked = ar._leaked_asset_id_digits(
            "Taped over the spring tension spacer; asset is operational.",
            asset_id="VOIDFILL0801",
            original_body="Taped changed and spacer added to control the spring tension.",
        )
        self.assertFalse(leaked)

    def test_single_digit_suffix_ignored_too_short_to_be_reliable(self):
        leaked = ar._leaked_asset_id_digits(
            "Checked the asset near bay 5 for damage.",
            asset_id="CR5",
            original_body="Checked the reach truck for damage.",
        )
        self.assertFalse(leaked)


if __name__ == "__main__":
    unittest.main()

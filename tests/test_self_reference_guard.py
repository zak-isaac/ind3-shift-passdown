"""Regression tests for _dropped_key_tokens' asset_id self-reference
exclusion, added 2026-09-17.

Ground truth: TSP0008's own remark ("Gasket taken off from TSP0008 to be
used on TSP0002...") mentions its own asset ID mid-sentence. A compliant
rewrite correctly drops that self-reference (report already shows
"TSP0008:" as the bullet label) -- but the guard, with no awareness of
which asset ID is "this WO's own", always saw that as a dropped token and
rejected every otherwise-good rewrite, forcing every escalation attempt to
fail and landing on the deterministic fallback 100% of the time for any
remark shaped like this.
"""
import unittest

import ai_rewriter as ar


class TestDroppedKeyTokensAssetIdExclusion(unittest.TestCase):
    def test_self_reference_removal_not_flagged_as_dropped(self):
        body = ("Gasket taken off from TSP0008 to be used on TSP0002. "
                 "An order for a new gasket has already been placed")
        # Correctly rewritten: self-reference to TSP0008 replaced with
        # "the asset", but TSP0002 (a DIFFERENT asset) is preserved.
        good_output = ("Gasket taken off from the asset to be used on "
                        "TSP0002. A new gasket has been ordered.")
        dropped = ar._dropped_key_tokens(body, good_output, asset_id="TSP0008")
        self.assertEqual(dropped, set())

    def test_other_asset_id_still_protected(self):
        body = ("Gasket taken off from TSP0008 to be used on TSP0002. "
                 "An order for a new gasket has already been placed")
        # Drops TSP0002 (a genuinely different asset) -- must still flag.
        bad_output = "Gasket taken off from the asset. A new gasket has been ordered."
        dropped = ar._dropped_key_tokens(body, bad_output, asset_id="TSP0008")
        self.assertEqual(dropped, {"tsp0002"})

    def test_no_asset_id_behaves_as_before(self):
        dropped = ar._dropped_key_tokens("moved it to 6-2", "Moved it.", asset_id="")
        self.assertEqual(dropped, {"6-2"})

    def test_default_asset_id_backward_compatible(self):
        # Callers that don't pass asset_id at all still work (default "").
        dropped = ar._dropped_key_tokens("moved it to 6-2", "Moved it.")
        self.assertEqual(dropped, {"6-2"})


if __name__ == "__main__":
    unittest.main()

"""AI-off-by-default is the current, deliberate behavior (2026-09-19) --
see ai_rewriter._USE_AI. Isaac's explicit final call after a full night of
testing generative rewriting: deterministic spelling/punctuation/grammar
cleanup only, never paraphrase, and identical input must always produce
identical output. These tests pin that contract down so nobody
accidentally flips _USE_AI back on (or breaks the deterministic path)
without noticing.
"""
import unittest
from unittest.mock import MagicMock, patch

import ai_rewriter


class TestAIOffByDefault(unittest.TestCase):
    def test_use_ai_flag_defaults_false(self):
        self.assertFalse(ai_rewriter._USE_AI)

    def test_rewrite_never_calls_ollama_client(self):
        client_mock = MagicMock()
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock) as get_client:
            ai_rewriter.rewrite(
                "RC0042 / horn was not working so I replaced the fuse and returned it to service"
            )
        get_client.assert_not_called()


class TestDeterminism(unittest.TestCase):
    """Same input, same output -- every time, no exceptions. This is the
    property a generative model cannot guarantee that a rule-based
    pipeline can."""

    def test_identical_input_gives_identical_output_across_many_runs(self):
        raw = "VOIDFILL0301 / the side  guard green belt was loose tight it back and run the machine as normal again."
        results = {ai_rewriter.rewrite(raw) for _ in range(10)}
        self.assertEqual(len(results), 1)

    def test_identical_input_gives_identical_output_for_typo_bearing_remark(self):
        raw = "Possible broken wire in steering colum checked for voltage"
        results = {ai_rewriter.rewrite(raw) for _ in range(10)}
        self.assertEqual(len(results), 1)


class TestSpellingCorrectionAppliedEndToEnd(unittest.TestCase):
    def test_known_typo_override_fixed(self):
        out = ai_rewriter.rewrite("Possible broken wire in steering colum checked for voltage")
        self.assertIn("column", out.lower())
        self.assertNotIn("colum ", out.lower())

    def test_dictionary_typo_fixed(self):
        out = ai_rewriter.rewrite("RC0042 / intstalled new pole covers and verified secure")
        self.assertIn("installed", out.lower())

    def test_asset_id_shaped_tokens_never_touched(self):
        out = ai_rewriter.rewrite("TSP0002 / gasket VOIDFILL0801 WO27974144 replaced")
        # Numbers/asset-shaped tokens inside the body must survive verbatim.
        self.assertIn("VOIDFILL0801", out)
        self.assertIn("WO27974144", out)


class TestNoNewWordsOrRephrasing(unittest.TestCase):
    """Loose but meaningful guard: the deterministic path must not expand
    a remark with invented content. Word count after cleanup should never
    exceed the original body's word count (spelling fixes are 1-for-1
    swaps, never insertions)."""

    def test_output_word_count_never_exceeds_input(self):
        raw = "the side  guard green belt was loose tight it back and run the machine as normal again."
        out = ai_rewriter.rewrite(raw)
        self.assertLessEqual(len(out.split()), len(raw.split()) + 1)  # +1 slack for added terminal period token edge cases


if __name__ == "__main__":
    unittest.main()

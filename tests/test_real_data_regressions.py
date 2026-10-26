"""Regression tests for real bugs found reviewing a full week of actual
shift data through the deterministic pipeline (2026-09-19). Each of these
is a confirmed WRONG correction seen in production output, not a
hypothetical -- see spelling_fix.py's JARGON_WHITELIST/_KNOWN_TYPO_OVERRIDES
comments for the exact wrong output each one used to produce.
"""
import unittest

import spelling_fix as sf


class TestContractionsNeverMangled(unittest.TestCase):
    """The most serious bug found: the tokenizer used to split "didn't"
    into "didn" + "'" + "t", treat "didn" as a misspelled word, and
    "correct" it to "did" -- silently turning "didn't" into "did't"."""

    def test_didnt_untouched(self):
        self.assertIn("didn't", sf.correct_spelling("the operator didn't have the taper inserted"))

    def test_wasnt_untouched(self):
        self.assertIn("wasn't", sf.correct_spelling("the fuse wasn't blown"))

    def test_couldnt_untouched(self):
        self.assertIn("couldn't", sf.correct_spelling("the belt couldn't hold tension"))


class TestJargonNoLongerMangled(unittest.TestCase):
    def test_retaped_untouched(self):
        out = sf.correct_spelling("retaped 1 of the threads on the asset")
        self.assertIn("retaped", out)
        self.assertNotIn("reaped", out)

    def test_comm_untouched(self):
        out = sf.correct_spelling("During inspection notice error comm on dash")
        self.assertIn("comm", out)
        self.assertNotIn(" come ", out)

    def test_refed_untouched(self):
        out = sf.correct_spelling("Refed the paper through the feeder")
        self.assertIn("Refed", out)
        self.assertNotIn("Refer", out)

    def test_ranpak_untouched(self):
        out = sf.correct_spelling("secured the RanPak head")
        self.assertIn("RanPak", out)
        self.assertNotIn("Rank", out)

    def test_tensioner_untouched(self):
        """Found 2026-10-26 live-testing the integration branch's actual
        Ollama round-trip: 'held tensioner while installing' came back as
        'held pensioner while installing' -- pyspellchecker's distance=1
        correction had no whitelisted maintenance term to prefer over the
        real-English word 'pensioner', a 1-letter edit away."""
        out = sf.correct_spelling("held the tensioner while installing the belt")
        self.assertIn("tensioner", out)
        self.assertNotIn("pensioner", out)


class TestKnownTypoOverridesFixed(unittest.TestCase):
    def test_tention_fixed(self):
        out = sf.correct_spelling("checked tention cleared faults")
        self.assertIn("tension", out)
        self.assertNotIn("mention", out)

    def test_adressed_fixed(self):
        out = sf.correct_spelling("Issue was already adressed before I got there")
        self.assertIn("addressed", out)
        self.assertNotIn("was already dressed", out)


if __name__ == "__main__":
    unittest.main()

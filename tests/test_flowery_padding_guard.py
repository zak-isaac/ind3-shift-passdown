"""Tests for _uses_flowery_padding_vocabulary, added 2026-09-17 after the
user explicitly asked for grammar-only cleanup with zero paraphrase and
zero meaning drift.

Real, live rejected output for WO#27974144 (VOIDFILL0801), given the clear
technician remark "paper feed got jammed up reset and recalibrated no
issues":

    "The voidfill process encountered an issue with a stuck-up paper
    feeder, however, after resetting and reconfiguring settings to
    accommodate this hiccup in operation without encountering further
    complications."

This introduced corporate filler ("hiccup", "accommodate") the technician
never used, AND silently swapped "recalibrated" (a specific, correct
action) for "reconfiguring settings" (a different action) -- a meaning
change disguised as a vocabulary upgrade. The existing _WRONG_DOMAIN_WORDS
guard doesn't cover this since none of those words are wrong-domain, just
vague/flowery padding.
"""
import unittest

import ai_rewriter as ar


class TestFlaweryPaddingVocabularyGuard(unittest.TestCase):
    def test_detects_real_rejected_voidfill0801_output(self):
        original = "paper feed got jammed up reset and recalibrated no issues"
        output = (
            "The voidfill process encountered an issue with a stuck-up paper "
            "feeder, however, after resetting and reconfiguring settings to "
            "accommodate this hiccup in operation without encountering "
            "further complications."
        )
        self.assertTrue(ar._uses_flowery_padding_vocabulary(original, output))

    def test_clean_grammar_only_rewrite_passes(self):
        original = "paper feed got jammed up reset and recalibrated no issues"
        output = "The paper feed jammed. It was reset and recalibrated with no issues."
        self.assertFalse(ar._uses_flowery_padding_vocabulary(original, output))

    def test_no_false_positive_when_technician_used_the_word(self):
        # If the technician's OWN remark already contains the word, it's not
        # a fabrication -- must not be flagged.
        original = "had to reconfigure the settings after the jam"
        output = "The settings were reconfigured after the jam."
        self.assertFalse(ar._uses_flowery_padding_vocabulary(original, output))

    def test_detects_reconfigur_substring_catches_all_forms(self):
        original = "belt was loose, tightened it back"
        for output in (
            "The belt was reconfigured.",
            "Reconfiguring the belt fixed it.",
            "The belt reconfiguration was completed.",
        ):
            with self.subTest(output=output):
                self.assertTrue(ar._uses_flowery_padding_vocabulary(original, output))

    def test_each_padding_word_individually_detected(self):
        original = "belt was loose, tightened it back and ran the machine normally"
        for word in ar._FLOWERY_PADDING_WORDS:
            with self.subTest(word=word):
                output = f"The belt issue involved a {word} during the process."
                self.assertTrue(ar._uses_flowery_padding_vocabulary(original, output))


if __name__ == "__main__":
    unittest.main()

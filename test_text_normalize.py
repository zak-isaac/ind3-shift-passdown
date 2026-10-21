import unittest

import text_normalize as tn


class TestAbbreviationExpansion(unittest.TestCase):
    def test_rts_expanded(self):
        self.assertEqual(tn.expand_abbreviations("unit rts"), "unit returned to service")

    def test_oos_expanded(self):
        self.assertEqual(tn.expand_abbreviations("tagged oos"), "tagged out of service")

    def test_does_not_touch_asset_ids(self):
        # PM is a real abbreviation, but must not fire inside a longer token.
        self.assertEqual(tn.expand_abbreviations("PM12345"), "PM12345")


class TestDoubleWordCollapse(unittest.TestCase):
    def test_simple_repeat_collapsed(self):
        self.assertEqual(tn.collapse_double_words("checked checked the wire"), "checked the wire")

    def test_case_insensitive_repeat_collapsed(self):
        self.assertEqual(tn.collapse_double_words("The the fuse blew"), "The fuse blew")

    def test_no_false_positive_on_distinct_words(self):
        self.assertEqual(tn.collapse_double_words("checked the wire"), "checked the wire")


class TestPunctuationSpacing(unittest.TestCase):
    def test_missing_space_after_comma(self):
        self.assertEqual(tn.fix_punctuation_spacing("wire,checked ok"), "wire, checked ok")

    def test_missing_space_after_semicolon(self):
        self.assertEqual(tn.fix_punctuation_spacing("done;verified"), "done; verified")

    def test_already_spaced_untouched(self):
        self.assertEqual(tn.fix_punctuation_spacing("wire, checked ok"), "wire, checked ok")


class TestSentenceCapitalization(unittest.TestCase):
    def test_capitalizes_after_period(self):
        self.assertEqual(
            tn.fix_sentence_capitalization("Fuse replaced. unit tested and returned."),
            "Fuse replaced. Unit tested and returned.",
        )

    def test_does_not_touch_decimals(self):
        self.assertEqual(tn.fix_sentence_capitalization("torque set to 3.5 nm"), "torque set to 3.5 nm")


if __name__ == "__main__":
    unittest.main()

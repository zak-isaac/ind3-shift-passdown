"""Regression test: CMMS-imported WO summaries must go through the same
spelling-fix pass as manually-typed entries.

Context: cetaris_parser.py pulls raw "Tech Remarks" text verbatim from a
Cetaris timecard export. summarizer.py's summarize_remarks() (Element LLM
or _smart_concat fallback) does zero spelling correction on its own -- it
only dedupes/joins/paraphrases. Before this fix, the `issue`/summary field
saved by main.py's _process_cmms_upload() was whatever summarize_remarks()
returned, unfixed -- so typos from the tech's raw CMMS remarks (e.g.
"tention", "retaped") would show up uncorrected in the tech's own upload
confirmation and "my entries" list, even though the FINAL shift report
(which re-pulls raw_remarks through ai_rewriter/spelling_fix at report-
generation time) would have corrected them. This test locks in that the
upload-time summary now gets the same treatment, closing that gap.
"""
import asyncio
import unittest

import spelling_fix
from summarizer import summarize_remarks


class TestCmmsSummarySpelling(unittest.TestCase):
    def _summarize_and_fix(self, remarks: list[str]) -> str:
        """Mirrors the exact composition now used in
        main.py's _process_cmms_upload()."""
        summary = asyncio.run(summarize_remarks(remarks))
        return spelling_fix.correct_spelling(summary)

    def test_single_remark_typo_corrected(self):
        # len(remarks) == 1 short-circuits summarize_remarks() to return
        # the remark verbatim -- spelling_fix must do the actual work here.
        result = self._summarize_and_fix(["checked tention on TSP0002"])
        self.assertIn("tension", result)
        self.assertNotIn("tention", result)

    def test_multi_remark_typo_survives_smart_concat_then_gets_fixed(self):
        result = self._summarize_and_fix([
            "retaped 1 of the threads",
            "adressed the loose gasket on the other side",
        ])
        self.assertIn("addressed", result)
        self.assertNotIn("adressed", result)
        # Shop jargon override must survive the trip through smart_concat --
        # "retaped" must NOT get corrected to "reaped".
        self.assertIn("retaped", result)

    def test_no_real_words_mangled_by_the_extra_pass(self):
        result = self._summarize_and_fix(["Replaced the motor brush on unit 4"])
        self.assertEqual(result, "Replaced the motor brush on unit 4")


if __name__ == "__main__":
    unittest.main()

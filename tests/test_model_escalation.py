"""Tests for ai_rewriter's model-escalation behavior in rewrite().

Real bug fixed 2026-09-16: when the primary (fastest) model's output got
rejected by a safety guard, rewrite() gave up immediately and fell back to
bare deterministic cleanup -- which, for the exact messy/run-on remarks
that most need AI help, made it look to the MST like "the AI isn't doing
anything". Proven live: phi4-mini fabricated an invented "circuit path" /
"suspect connector" narrative for a real M2784 remark, correctly got
rejected by _dropped_part_nouns, and the MST saw the untouched raw text.

Fix: on rejection, retry with the next-better model in _AVAILABLE_MODELS
(already ordered fast-but-riskier -> slow-but-more-reliable) before giving
up to the deterministic fallback. These tests mock _call_ollama so they
run instantly and deterministically, no live Ollama required.
"""
import unittest
from unittest.mock import patch

import ai_rewriter


# A body >8 words (clears the AI-eligibility threshold) that contains a
# _PRESERVE_PART_NOUNS word ("fuse") -- lets us reliably trigger
# _dropped_part_nouns on a canned "bad" AI response.
BODY = ("checked horn fuse and checked with new button and no results "
        "found anywhere on this unit")
INPUT_TEXT = f"M2784 / {BODY}"

BAD_RESPONSE_DROPS_FUSE = (
    "Checked the horn circuitry and tested with a new button; no results "
    "were found anywhere on this unit."
)
GOOD_RESPONSE_KEEPS_FUSE = (
    "Checked horn fuse and tested with new button; no results found "
    "anywhere on this unit."
)


class TestModelEscalation(unittest.TestCase):
    """Requires _USE_AI=True since AI is off by default (2026-09-19,
    deterministic-only per Isaac's explicit call) -- this whole class
    tests the dormant AI escalation code path, kept intact for a future
    re-enable, just not exercised by default anymore.
    """

    def setUp(self):
        self._orig_available = list(ai_rewriter._AVAILABLE_MODELS)
        self._orig_model = ai_rewriter._MODEL
        ai_rewriter._AVAILABLE_MODELS = ["phi4-mini", "llama3.1:8b", "phi4:14b"]
        ai_rewriter._USE_AI = True

    def tearDown(self):
        ai_rewriter._AVAILABLE_MODELS = self._orig_available
        ai_rewriter._MODEL = self._orig_model
        ai_rewriter._USE_AI = False

    def test_primary_success_calls_ollama_once(self):
        with patch.object(ai_rewriter, "_call_ollama", return_value=GOOD_RESPONSE_KEEPS_FUSE) as mock_call:
            out = ai_rewriter.rewrite(INPUT_TEXT)
        self.assertEqual(mock_call.call_count, 1)
        self.assertIn("fuse", out.lower())
        self.assertEqual(mock_call.call_args.kwargs.get("model"), "phi4-mini")

    def test_rejected_primary_escalates_to_next_model(self):
        with patch.object(
            ai_rewriter, "_call_ollama",
            side_effect=[BAD_RESPONSE_DROPS_FUSE, GOOD_RESPONSE_KEEPS_FUSE],
        ) as mock_call:
            out = ai_rewriter.rewrite(INPUT_TEXT)
        self.assertEqual(mock_call.call_count, 2)
        models_used = [c.kwargs.get("model") for c in mock_call.call_args_list]
        self.assertEqual(models_used, ["phi4-mini", "llama3.1:8b"])
        self.assertIn("fuse", out.lower())

    def test_all_models_rejected_falls_back_to_deterministic(self):
        with patch.object(
            ai_rewriter, "_call_ollama",
            side_effect=[BAD_RESPONSE_DROPS_FUSE, BAD_RESPONSE_DROPS_FUSE, BAD_RESPONSE_DROPS_FUSE],
        ) as mock_call:
            out = ai_rewriter.rewrite(INPUT_TEXT)
        self.assertEqual(mock_call.call_count, 3)
        # Deterministic fallback preserves the technician's own wording,
        # including the part noun the AI kept dropping.
        self.assertIn("fuse", out.lower())
        self.assertIn("horn fuse", out.lower())

    def test_legitimate_nothing_to_report_does_not_escalate(self):
        with patch.object(ai_rewriter, "_call_ollama", return_value="-") as mock_call:
            out = ai_rewriter.rewrite(INPUT_TEXT)
        self.assertEqual(mock_call.call_count, 1)
        self.assertEqual(out, "")

    def test_empty_available_models_falls_back_to_single_global_model(self):
        ai_rewriter._AVAILABLE_MODELS = []
        ai_rewriter._MODEL = "phi4-mini"
        with patch.object(ai_rewriter, "_call_ollama", return_value=GOOD_RESPONSE_KEEPS_FUSE) as mock_call:
            out = ai_rewriter.rewrite(INPUT_TEXT)
        self.assertEqual(mock_call.call_count, 1)
        self.assertEqual(mock_call.call_args.kwargs.get("model"), "phi4-mini")
        self.assertIn("fuse", out.lower())

    def test_garbage_output_escalates_same_as_guard_rejection(self):
        with patch.object(
            ai_rewriter, "_call_ollama",
            side_effect=[None, GOOD_RESPONSE_KEEPS_FUSE],
        ) as mock_call:
            out = ai_rewriter.rewrite(INPUT_TEXT)
        self.assertEqual(mock_call.call_count, 2)
        self.assertIn("fuse", out.lower())


class TestIsGarbageDashSignal(unittest.TestCase):
    """Regression test for the dead-code bug found while building escalation
    tests: _is_garbage()'s len(text) < 10 check always caught a bare "-"
    before the dedicated dash-handling logic downstream ever saw it, so the
    AI's "nothing worth reporting" signal was silently indistinguishable
    from actual garbage output."""

    def test_bare_dash_is_not_garbage(self):
        self.assertFalse(ai_rewriter._is_garbage("-"))
        self.assertFalse(ai_rewriter._is_garbage("--"))

    def test_short_real_garbage_still_flagged(self):
        self.assertTrue(ai_rewriter._is_garbage("ok."))
        self.assertTrue(ai_rewriter._is_garbage("n/a"))


if __name__ == "__main__":
    unittest.main()

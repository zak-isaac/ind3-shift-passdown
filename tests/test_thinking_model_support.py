"""Tests for thinking-model support (Qwen3 family) in ai_rewriter.py.

Root cause found while evaluating qwen3-4b for production use (2026-09-17):
1. Qwen3 emits a <think>...</think> reasoning block before its real answer.
   The production num_predict budget (180-400 tokens, tuned for non-thinking
   models like phi4-mini) is nowhere near enough for that scratchpad, so the
   raw response was truncated mid-thought and never reached an answer.
2. Even with /no_think appended to skip reasoning, the pipeline's own bare
   "\\n\\n" stop sequence collided with Qwen3's own required
   "<think>\\n\\n</think>\\n\\n" formatting -- proven live, raw output was the
   literal 4-character string "<think>" and nothing else, because the stop
   sequence fired immediately after the opening tag.

Fix: _THINKING_MODELS registry controls two behaviors in
_rewrite_with_model() -- append "/no_think" to the prompt, and drop the
bare "\\n\\n" stop sequence -- plus unconditional _strip_think_block() on
raw output before any guard runs (safe no-op for non-thinking models).
"""
import sys
import unittest
from unittest.mock import MagicMock, patch

import pathlib
_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


# Import the installed httpx package normally. A previous test helper
# placed a small fake in sys.modules, globally breaking FastAPI/TestClient
# when all tests were collected together. Test methods mock the HTTP client
# per call instead, keeping the real module import intact.
import ai_rewriter  # noqa: E402


def _make_ok_response(text: str) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": text}
    return resp


class TestStripThinkBlock(unittest.TestCase):
    def test_strips_think_block_with_content(self):
        raw = "<think>\nOkay let me tackle this query...\n</think>\n\nFuse replaced and unit returned to service."
        self.assertEqual(
            ai_rewriter._strip_think_block(raw),
            "Fuse replaced and unit returned to service.",
        )

    def test_strips_empty_think_block(self):
        raw = "<think>\n\n</think>\n\nHorn fuse replaced."
        self.assertEqual(ai_rewriter._strip_think_block(raw), "Horn fuse replaced.")

    def test_no_think_block_is_noop(self):
        raw = "Horn fuse replaced and unit returned to service."
        self.assertEqual(ai_rewriter._strip_think_block(raw), raw)

    def test_none_input_returns_empty_string(self):
        self.assertEqual(ai_rewriter._strip_think_block(None), "")

    def test_truncated_think_block_with_no_closing_tag_is_not_stripped(self):
        """Regex requires a closing tag -- a response cut off mid-think
        (proven live: raw output was just '<think>') is NOT silently
        turned into something that looks like a real answer. It should
        still read as garbage/incomplete downstream."""
        raw = "<think>\nOkay let me tackle this query"
        self.assertEqual(ai_rewriter._strip_think_block(raw), raw)


class TestThinkingModelPromptAdjustment(unittest.TestCase):
    """_rewrite_with_model() must append /no_think and drop the "\\n\\n"
    stop sequence ONLY for models registered in _THINKING_MODELS -- must
    not change behavior for existing non-thinking models.

    Requires _USE_AI=True since AI is off by default -- these tests
    exercise the dormant AI prompt-building code directly.
    """

    def setUp(self):
        ai_rewriter._client = None
        ai_rewriter._USE_AI = True

    def tearDown(self):
        ai_rewriter._USE_AI = False

    def _patch_post(self, return_text: str):
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(return_text)
        return client_mock, patch.object(ai_rewriter, "_get_client", return_value=client_mock)

    def test_thinking_model_gets_no_think_and_drops_double_newline_stop(self):
        client_mock, patcher = self._patch_post("<think>\n\n</think>\n\nFuse replaced.")
        with patcher:
            ai_rewriter._AVAILABLE_MODELS = ["qwen3-4b"]
            ai_rewriter._MODEL = "qwen3-4b"
            ai_rewriter.rewrite(
                "RC0042 / horn was not working so I replaced the fuse and returned it to service"
            )
        sent = client_mock.post.call_args.kwargs["json"]
        self.assertTrue(sent["prompt"].rstrip().endswith("/no_think"))
        self.assertNotIn("\n\n", sent["options"]["stop"])

    def test_non_thinking_model_unaffected(self):
        client_mock, patcher = self._patch_post("Fuse replaced and unit returned to service.")
        with patcher:
            ai_rewriter._AVAILABLE_MODELS = ["phi4-mini"]
            ai_rewriter._MODEL = "phi4-mini"
            ai_rewriter.rewrite(
                "RC0042 / horn was not working so I replaced the fuse and returned it to service"
            )
        sent = client_mock.post.call_args.kwargs["json"]
        self.assertFalse(sent["prompt"].rstrip().endswith("/no_think"))
        self.assertIn("\n\n", sent["options"]["stop"])

    def tearDown(self):
        ai_rewriter._AVAILABLE_MODELS = []
        ai_rewriter._MODEL = "phi4-mini"


class TestThinkingModelEndToEnd(unittest.TestCase):
    """A raw response with a full <think> block must be cleaned down to
    just the real answer, going through the exact same guard chain as
    every other model -- not a special-cased bypass."""

    def setUp(self):
        ai_rewriter._client = None
        ai_rewriter._AVAILABLE_MODELS = ["qwen3-4b"]
        ai_rewriter._MODEL = "qwen3-4b"

    def tearDown(self):
        ai_rewriter._AVAILABLE_MODELS = []
        ai_rewriter._MODEL = "phi4-mini"

    def test_think_block_stripped_before_guards_run(self):
        raw = (
            "<think>\nOkay, the user wants a professional passdown entry. "
            "Let me rewrite this concisely.\n</think>\n\n"
            "RC0042 / Horn was not working so the fuse was replaced and the unit was returned to service."
        )
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(raw)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(
                "RC0042 / horn was not working so I replaced the fuse and returned it to service"
            )
        self.assertNotIn("<think>", result)
        self.assertNotIn("Okay, the user wants", result)
        self.assertNotIn("RC0042", result)  # asset ID still stripped, same as every other model
        self.assertIn("fuse", result.lower())

    def test_think_only_response_with_no_real_answer_is_treated_as_garbage(self):
        """If the model's whole budget got eaten by thinking and nothing
        real follows, this must fall back -- not ship an empty/garbage
        remark as if it were a legitimate 'nothing to report' signal."""
        raw = "<think>\nOkay, let me tackle this query. The user wants me to rewrite...\n</think>\n\n"
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(raw)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(
                "RC0042 / horn was not working so I replaced the fuse and returned it to service"
            )
        # Falls back to the deterministic path, which preserves the
        # technician's own words rather than shipping nothing.
        self.assertIn("fuse", result.lower())


if __name__ == "__main__":
    unittest.main()

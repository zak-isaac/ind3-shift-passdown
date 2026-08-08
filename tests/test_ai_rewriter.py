"""Tests for ai_rewriter.py.

Covers:
  - Short remarks
  - Long remarks
  - Equipment down remarks
  - Pending work
  - Completed work
  - Grammar improvement pass-through
  - No information loss contract
  - Fallback: Ollama unavailable (ConnectError)
  - Fallback: model missing (empty models list)
  - Fallback: timeout
  - Fallback: unexpected exception
  - Fallback: empty response from model
  - Empty / whitespace input passthrough

Run from the IND3-Passdown-Server directory:
    python -m pytest tests/test_ai_rewriter.py -v
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Ensure the package root is on sys.path when running from repo root
# ---------------------------------------------------------------------------
import pathlib
_ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _make_httpx_mock():
    """Return a minimal stub for the httpx module so ai_rewriter imports cleanly."""
    mod = types.ModuleType("httpx")
    mod.Client = MagicMock
    mod.Timeout = MagicMock(return_value=MagicMock())
    mod.TimeoutException = type("TimeoutException", (Exception,), {})
    mod.ConnectError    = type("ConnectError",    (Exception,), {})
    return mod


# Inject stub httpx before importing ai_rewriter so we control transport
sys.modules.setdefault("httpx", _make_httpx_mock())
import ai_rewriter  # noqa: E402  (must come after sys.modules patch)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ok_response(text: str) -> MagicMock:
    """Build a fake httpx Response that returns `text` as the AI output."""
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"response": text}
    return resp


def _make_tags_response(model_names: list[str]) -> MagicMock:
    resp = MagicMock()
    resp.raise_for_status = MagicMock()
    resp.json.return_value = {"models": [{"name": n} for n in model_names]}
    return resp


# ---------------------------------------------------------------------------
# Tests: rewrite()
# ---------------------------------------------------------------------------

class TestRewriteSuccess(unittest.TestCase):
    """AI is available and returns polished text."""

    def setUp(self):
        # Reset singleton client before each test
        ai_rewriter._client = None

    def _patch_post(self, return_text: str):
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(return_text)
        return patch.object(ai_rewriter, "_get_client", return_value=client_mock)

    def test_short_remark_returns_ai_text(self):
        ai_out = "RC0042 horn fuse replaced and verified operational."
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite("RC0042 horn not working replaced fuse")
        self.assertEqual(result, ai_out)

    def test_long_remark_returns_ai_text(self):
        raw = (
            "VOIDFILL0701 bottom taper not cutting correctly. "
            "Loose cutter pad found interfering with blade. "
            "Replaced cutter pad. Recalibrated side guides. "
            "Verified operation. Cleared faults. Equipment returned to service."
        )
        ai_out = (
            "VOIDFILL0701 bottom taper operation was restored after replacing a "
            "loose cutter pad that was interfering with the blade. Side guides were "
            "recalibrated, operation verified, all faults cleared, and equipment "
            "returned to normal service."
        )
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, ai_out)

    def test_equipment_down_remark(self):
        raw = "WAV0015 battery not holding charge unit is OOS"
        ai_out = "WAV0015 is out of service due to a battery that is not holding a charge."
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, ai_out)

    def test_pending_work_remark(self):
        raw = "RC0033 hydraulic leak under mast. Parts on order. Unit tagged OOS."
        ai_out = (
            "RC0033 has a hydraulic leak under the mast. Parts have been ordered "
            "and the unit is tagged out of service pending repair."
        )
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, ai_out)

    def test_completed_work_remark(self):
        raw = "BALER0002 tightened bale wire tension. tested. rts."
        ai_out = "BALER0002 bale wire tension was adjusted and tested; unit returned to service."
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, ai_out)

    def test_grammar_improvement_changes_text(self):
        raw = "forklift rc12 the forks was bent we straighted it and return back to service ok"
        ai_out = (
            "RC12 forklift forks were found bent. The forks were straightened and "
            "the unit was returned to service."
        )
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite(raw)
        self.assertNotEqual(result, raw)
        self.assertEqual(result, ai_out)

    def test_ai_output_preserves_asset_name(self):
        raw = "CONV0055 belt slipping replaced belt rts"
        ai_out = "CONV0055 conveyor belt was replaced after slipping was identified; unit returned to service."
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite(raw)
        self.assertIn("CONV0055", result)

    def test_ai_output_preserves_technical_detail(self):
        raw = "RC0042 horn fuse blown replaced 10A fuse rts"
        ai_out = "RC0042 horn inoperative due to a blown 10A fuse. Fuse replaced and unit returned to service."
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite(raw)
        self.assertIn("10A", result)


# ---------------------------------------------------------------------------
# Tests: fallback behaviour
# ---------------------------------------------------------------------------

class TestRewriteFallback(unittest.TestCase):
    """AI is unavailable — rewrite() must return the original text unchanged."""

    def setUp(self):
        ai_rewriter._client = None

    def _make_failing_client(self, exc: Exception) -> MagicMock:
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.side_effect = exc
        return client_mock

    def test_ollama_connect_error_returns_original(self):
        raw = "RC0042 horn broken fixed rts"
        client = self._make_failing_client(ai_rewriter.httpx.ConnectError("refused"))
        with patch.object(ai_rewriter, "_get_client", return_value=client):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, raw)

    def test_timeout_returns_original(self):
        raw = "WAV0015 battery dead"
        client = self._make_failing_client(ai_rewriter.httpx.TimeoutException("timed out"))
        with patch.object(ai_rewriter, "_get_client", return_value=client):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, raw)

    def test_unexpected_exception_returns_original(self):
        raw = "BALER0001 jam cleared"
        client = self._make_failing_client(RuntimeError("unexpected"))
        with patch.object(ai_rewriter, "_get_client", return_value=client):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, raw)

    def test_empty_response_returns_original(self):
        raw = "CONV0033 motor replaced rts"
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response("")  # empty AI response
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, raw)

    def test_empty_input_returns_empty(self):
        self.assertEqual(ai_rewriter.rewrite(""), "")

    def test_whitespace_only_input_returned_unchanged(self):
        self.assertEqual(ai_rewriter.rewrite("   "), "   ")


# ---------------------------------------------------------------------------
# Tests: check_ollama()
# ---------------------------------------------------------------------------

class TestCheckOllama(unittest.TestCase):

    def setUp(self):
        ai_rewriter._client = None

    def test_returns_true_when_model_present(self):
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.get.return_value = _make_tags_response(["phi4-mini:latest"])
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.check_ollama()
        self.assertTrue(result)

    def test_returns_false_when_model_missing(self):
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.get.return_value = _make_tags_response(["llama3:latest"])
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.check_ollama()
        self.assertFalse(result)

    def test_returns_false_when_ollama_down(self):
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.get.side_effect = ai_rewriter.httpx.ConnectError("refused")
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.check_ollama()
        self.assertFalse(result)

    def test_returns_false_on_unexpected_error(self):
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.get.side_effect = RuntimeError("boom")
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.check_ollama()
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()

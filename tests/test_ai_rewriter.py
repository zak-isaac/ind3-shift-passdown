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
    """AI is available and returns polished text.

    2026-09-19: AI is OFF by default now (ai_rewriter._USE_AI = False,
    deterministic-only per Isaac's explicit call), so these tests -- which
    specifically exercise the AI code path -- opt back in for their own
    scope and restore the real default afterward.
    """

    def setUp(self):
        # Reset singleton client before each test
        ai_rewriter._client = None
        ai_rewriter._USE_AI = True

    def tearDown(self):
        ai_rewriter._USE_AI = False

    def _patch_post(self, return_text: str):
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(return_text)
        return patch.object(ai_rewriter, "_get_client", return_value=client_mock)

    def test_short_remark_returns_ai_text(self):
        # Body must exceed _MIN_WORDS_FOR_AI (8) or rewrite() short-circuits
        # to the deterministic _simple_cleanup() path and never calls Ollama
        # at all -- see TestShortRemarkBypass below for that behavior.
        ai_out = "RC0042 horn fuse replaced and verified operational."
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite("RC0042 horn was not working properly so the fuse was replaced")
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
        raw = "WAV0015 battery is not holding charge and unit is OOS"
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
        raw = "BALER0002 tightened bale wire tension then tested it and returned to service"
        # _clean_output() intentionally normalizes '; ' -> ', ' -- semicolons
        # in a passdown entry read like awkward transcription, not like how
        # a technician actually talks. Mock the AI response with one to
        # confirm the code-level cleanup still runs on top of the AI output.
        ai_out = "BALER0002 bale wire tension was adjusted and tested; unit returned to service."
        expected = "BALER0002 bale wire tension was adjusted and tested, unit returned to service."
        with self._patch_post(ai_out):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, expected)

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
        # Body must exceed _MIN_WORDS_FOR_AI or this never touches the
        # (mocked) client at all -- see TestShortRemarkBypass.
        raw = "RC0042 horn was broken then it was fixed and returned to service"
        client = self._make_failing_client(ai_rewriter.httpx.ConnectError("refused"))
        with patch.object(ai_rewriter, "_get_client", return_value=client):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, raw + ".")

    def test_timeout_returns_original(self):
        raw = "WAV0015 battery was not holding a charge and unit is out of service"
        client = self._make_failing_client(ai_rewriter.httpx.TimeoutException("timed out"))
        with patch.object(ai_rewriter, "_get_client", return_value=client):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, raw + ".")

    def test_unexpected_exception_returns_original(self):
        raw = "BALER0001 paper jam was cleared and unit was tested and returned to service"
        client = self._make_failing_client(RuntimeError("unexpected"))
        with patch.object(ai_rewriter, "_get_client", return_value=client):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, raw + ".")

    def test_empty_response_returns_original(self):
        raw = "CONV0033 drive motor was replaced and unit was tested then returned to service"
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response("")  # empty AI response
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, raw + ".")

    def test_fallback_strips_first_person(self):
        """Rule 6 must hold even when the AI fails -- a timed-out/garbage
        response must not leak the technician's raw \"I\"/\"we\" wording."""
        raw = "RC0050 / Horn was not working properly, so I replaced the failed horn fuse and verified operation"
        client = self._make_failing_client(ai_rewriter.httpx.TimeoutException("timed out"))
        with patch.object(ai_rewriter, "_get_client", return_value=client):
            result = ai_rewriter.rewrite(raw)
        self.assertNotIn(" I ", result)
        self.assertNotIn("RC0050", result)
        self.assertTrue(result.endswith("."))

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


class TestShortRemarkBypass(unittest.TestCase):
    """Remarks at or under _MIN_WORDS_FOR_AI (single remark, no ' | ')
    never touch the AI at all -- deterministic cleanup only. This is
    intentional (small local models reliably pad short remarks with
    fabricated detail), so pin the behavior down explicitly instead of
    letting it hide inside AI-mocked tests that never actually exercise it.
    """

    def setUp(self):
        ai_rewriter._client = None

    def test_never_calls_client(self):
        client_mock = MagicMock()
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock) as get_client:
            result = ai_rewriter.rewrite("RC0042 horn broken fixed rts")
        get_client.assert_not_called()
        # "rts" -> "returned to service" via text_normalize's shop-abbreviation
        # expansion (2026-09-19) -- deterministic, not an AI call.
        self.assertEqual(result, "RC0042 horn broken fixed returned to service.")

    def test_pipe_separated_remarks_always_use_ai_even_if_short(self):
        """' | ' means multiple sequential remarks -- always synthesize via
        AI regardless of word count, per build_input()'s contract. Requires
        _USE_AI=True since AI is off by default (see TestRewriteSuccess).
        """
        ai_rewriter._USE_AI = True
        self.addCleanup(setattr, ai_rewriter, "_USE_AI", False)
        ai_out = "Fuse replaced. Unit tested and returned to service."
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(ai_out)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock) as get_client:
            result = ai_rewriter.rewrite("RC0042 / Fuse replaced | Tested rts")
        get_client.assert_called()
        self.assertEqual(result, ai_out)


class TestEquipmentTypeScrubber(unittest.TestCase):
    """Deterministic net for the 'invented vehicle/car/truck/forklift label'
    bug -- proven live on an M-prefixed asset the model called 'our
    heavy-duty vehicle' despite the prompt saying not to guess."""

    def test_strips_invented_vehicle_word_for_unknown_prefix(self):
        out = ai_rewriter._strip_invented_equipment_type(
            "Loose connection found on our heavy-duty vehicle causing lighting issues.",
            "M2678",
            "Checked dashboard for faulty connections on M2678.",
        )
        self.assertNotIn("vehicle", out.lower())
        self.assertIn("the asset", out.lower())

    def test_leaves_confirmed_vehicle_asset_alone(self):
        out = ai_rewriter._strip_invented_equipment_type(
            "Brake test completed on the vehicle; returned to service.",
            "TSP0008",
            "Brake test done.",
        )
        self.assertIn("vehicle", out.lower())

    def test_leaves_word_alone_if_technician_said_it(self):
        out = ai_rewriter._strip_invented_equipment_type(
            "The truck was inspected and returned to service.",
            "M9001",
            "Inspected the truck, all good.",
        )
        self.assertIn("truck", out.lower())


class TestVagueEquipmentPhraseScrubber(unittest.TestCase):
    """Proven live on M2355's coolant remark -- the model hedged with
    'an unidentified piece of equipment' instead of 'the asset' (rule 26)."""

    def test_strips_unidentified_piece_of_equipment(self):
        out = ai_rewriter._strip_vague_equipment_phrase(
            "Coolant added to an unidentified piece of equipment after checks."
        )
        self.assertNotIn("unidentified", out.lower())
        self.assertIn("the asset", out.lower())


class TestLeakedContextLineScrubber(unittest.TestCase):
    """THE big one: an early build's private 'Equipment context (background
    only): ...' prompt preamble was parroted back by the model and shipped
    in a real shift passdown as if it were a technician's own sub-bullet
    (VOIDFILL1001, Friday 9/11 S5 shift). Never again."""

    def test_strips_leaked_context_preamble(self):
        leaked = (
            "The misalignment of a critical component was rectified by "
            "repositioning its associated Pe sensor correctly, subsequent "
            "checks confirmed full functionality and operation readiness for "
            "the asset in question. Equipment context (background only): "
            "This asset is NOT a vehicle, car, truck, or forklift -- never "
            "call it one. Refer to it only as the asset, it, or whatever word "
            "the technician's own remark used."
        )
        out = ai_rewriter._strip_leaked_context_line(leaked)
        self.assertNotIn("Equipment context", out)
        self.assertNotIn("background only", out)
        self.assertTrue(out.startswith("The misalignment"))

    def test_full_leak_collapses_to_empty(self):
        """When an AI call's ENTIRE output is the leaked preamble (no real
        content at all -- exactly what happened live), rewrite() must drop
        it instead of shipping a fake sub-bullet with prompt internals.
        Requires _USE_AI=True since AI is off by default.
        """
        ai_rewriter._USE_AI = True
        self.addCleanup(setattr, ai_rewriter, "_USE_AI", False)
        leaked_only = (
            "Equipment context (background only): This asset is NOT a "
            "vehicle, car, truck, or forklift -- never call it one. Refer to "
            "it only as the asset, it, or whatever word the technician's own "
            "remark used."
        )
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(leaked_only)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(
                "VOIDFILL1001 / PE sensor misaligned then repositioned and "
                "rechecked for proper alignment and full functionality"
            )
        self.assertEqual(result, "")


class TestTruncationDetection(unittest.TestCase):
    """A dangling space before the final period means generation got cut
    off mid-word (proven live: '...restoration to its standard .') --
    _is_garbage() must catch it so the fallback kicks in instead of
    shipping a visibly broken sentence."""

    def test_dangling_period_is_garbage(self):
        self.assertTrue(ai_rewriter._is_garbage(
            "Servos calibrated, restoring the asset to standard ."
        ))

    def test_normal_sentence_is_not_garbage(self):
        self.assertFalse(ai_rewriter._is_garbage(
            "Servos calibrated, restoring the asset to standard operation."
        ))


class TestFirstPersonAppliedOnSuccessPath(unittest.TestCase):
    """Ground-truth finding (real production DB, Friday 9/11 S5 shift):
    the model invents 'we're'/'I found' narration from scratch on
    SUCCESSFUL calls fed already-clean, already-impersonal raw remarks.
    First-person stripping must run on the happy path too, not just the
    failure/fallback path."""

    def setUp(self):
        ai_rewriter._client = None

    def test_ai_invented_first_person_gets_stripped(self):
        raw = "Both M504 and M507 PEs were found to be misaligned then M504 was realigned and verified"
        ai_out = "I found that both M504 and M507 PEs were misaligned; we realigned M504 and verified it."
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(ai_out)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(raw)
        self.assertNotIn(" I ", result)
        self.assertNotIn("we ", result.lower())


class TestKeyTokenPreservationGuard(unittest.TestCase):
    """Ground-truth finding: the model silently dropped a real destination
    code ('moved it to 6-2') from a real report. If the AI's output is
    missing a digit-bearing token (part ID, location code, measurement)
    that was in the input, don't trust that output -- fall back to the
    deterministic, nothing-lost version instead.

    Requires _USE_AI=True since AI is off by default -- this class tests
    the AI-vs-guard interaction specifically.
    """

    def setUp(self):
        ai_rewriter._client = None
        ai_rewriter._USE_AI = True

    def tearDown(self):
        ai_rewriter._USE_AI = False

    def test_dropped_token_triggers_deterministic_fallback(self):
        raw = "Removed the box with the bad barcode and moved it to 6-2 then cleared faults and monitored"
        ai_out = "Box with the bad barcode removed and relocated for accurate processing; faults cleared and monitored."
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(ai_out)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(raw)
        self.assertIn("6-2", result)
        self.assertNotEqual(result, ai_out)

    def test_all_tokens_preserved_keeps_ai_output(self):
        raw = "Removed the box with the bad barcode and moved it to 6-2 then cleared faults and monitored"
        ai_out = "Box with the bad barcode removed and relocated to 6-2; faults cleared and monitored."
        expected = "Box with the bad barcode removed and relocated to 6-2, faults cleared and monitored."
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(ai_out)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(raw)
        self.assertEqual(result, expected)


class TestUnwarrantedResolutionGuard(unittest.TestCase):
    """THE dangerous one: real production output turned an explicitly
    unresolved problem ('Unable to complete the test due to bad load
    wheels') into a fully resolved, verified one ('...swapped with new
    ones, and a 30-box test was performed to verify proper operation').
    Token-diffing can't catch this -- nothing was dropped, content was
    invented. Dedicated contradiction check instead."""

    def setUp(self):
        ai_rewriter._client = None

    def test_detects_invented_resolution(self):
        self.assertTrue(ai_rewriter._claims_unwarranted_resolution(
            "Unable to complete the test due to bad load wheels",
            "The load wheels were worn and were replaced; a 30-box test "
            "verified proper operation.",
        ))

    def test_allows_output_that_preserves_the_incomplete_signal(self):
        self.assertFalse(ai_rewriter._claims_unwarranted_resolution(
            "Unable to complete the test due to bad load wheels",
            "Unable to complete the test due to bad load wheels.",
        ))

    def test_rewrite_falls_back_when_resolution_is_fabricated(self):
        raw = "Unable to complete the test due to bad load wheels on this unit today"
        ai_out = (
            "The load wheels were worn and were replaced with new ones; a "
            "30-box test was performed to verify proper operation."
        )
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(ai_out)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(raw)
        self.assertIn("unable", result.lower())
        self.assertNotIn("30-box", result)


class TestDanglingWordTruncation(unittest.TestCase):
    """Proven live: 'the dashboard was returned to.' -- cut off mid-phrase
    with no dangling space, so the original truncation regex missed it."""

    def test_dangling_preposition_is_garbage(self):
        self.assertTrue(ai_rewriter._is_garbage("The dashboard was returned to."))

    def test_complete_sentence_is_not_garbage(self):
        self.assertFalse(ai_rewriter._is_garbage("The dashboard was returned to service."))


class TestAssistedWithOnEveryPath(unittest.TestCase):
    """Rule 7 ('Assisted with X' -> direct action) is purely mechanical and
    zero-fabrication-risk, so it must run on the short-remark bypass and the
    fallback path too, not just the AI-success path. Proven live on real
    shift data: 'Assisted with installing new brushes on the flex' (exactly
    8 words) hit the bypass and shipped completely unchanged."""

    def test_short_remark_bypass_still_normalizes_assisted_with(self):
        result = ai_rewriter._simple_cleanup("Assisted with installing new brushes on the flex")
        self.assertNotIn("Assisted", result)
        self.assertIn("Installed", result)

    def test_deterministic_fallback_still_normalizes_assisted_with(self):
        result = ai_rewriter._deterministic_fallback("Assisted with replacing the sensor", "")
        self.assertNotIn("Assisted", result)
        self.assertIn("Replaced", result)

    def test_bare_assist_with_no_suffix_is_normalized(self):
        """Real raw remark used the bare imperative 'Assist with', not
        'Assisted'/'Assisting' -- the original regex required an -ed/-ing
        suffix and missed this form entirely."""
        result = ai_rewriter._normalize_assisted_with("Assist with 2 baler wires")
        self.assertNotIn("Assist", result)

    def test_please_assist_me_prefix_is_stripped(self):
        """Proven live: old prompt let the AI invent 'Please assist me in
        replacing...' out of a bare 'Assist with' remark. The fallback path
        must not ship that framing either."""
        result = ai_rewriter._normalize_assisted_with("Please assist me in replacing two wires")
        self.assertNotIn("Please assist me", result)


class TestSameEventDedup(unittest.TestCase):
    """Deterministic pre-dedup for multi-tech same-WO remarks. Proven live:
    neither llama3.1:8b nor phi4:14b reliably merge 'replaced a bad brush on
    motor 4' + 'installed a new brush for the motor' into one statement --
    both kept them as two redundant sentences even with an explicit prompt
    rule. This handles it deterministically before the AI ever sees it."""

    def test_collapses_same_event_different_wording(self):
        result = ai_rewriter._dedupe_same_event_remarks([
            "had to replace a bad brush on motor 4 with spare motors",
            "Assisted with installing a new brush for the motor.",
        ])
        self.assertEqual(len(result), 1)

    def test_keeps_genuinely_different_actions(self):
        result = ai_rewriter._dedupe_same_event_remarks([
            "Replaced lower brushes on motors 9,5,4",
            "New load wheels added.",
        ])
        self.assertEqual(len(result), 2)

    def test_never_drops_a_remark_with_a_unique_number(self):
        """Safety rail: even with high word overlap, never collapse if the
        remark being dropped carries a numeric/identifier token the kept
        one doesn't have -- that could be a part number or quantity."""
        result = ai_rewriter._dedupe_same_event_remarks([
            "Replaced the brush on the motor",
            "Replaced the brush on the motor, part number 72553",
        ])
        self.assertEqual(len(result), 2)

    def test_different_motor_numbers_are_not_collapsed(self):
        result = ai_rewriter._dedupe_same_event_remarks([
            "Replaced a bad brush on motor 4",
            "Replaced a bad brush on motor 9",
        ])
        self.assertEqual(len(result), 2)

    def test_build_input_applies_dedup(self):
        inp = ai_rewriter.build_input("FLEX0007", [
            "had to replace a bad brush on motor 4 with spare motors",
            "Assisted with installing a new brush for the motor.",
        ])
        self.assertNotIn(" | ", inp)


class TestPartNounPreservationGuard(unittest.TestCase):
    """Ground-truth finding: both local models turned 'fittings' (a specific
    part name) into 'components' (a vague generic word) -- same class of bug
    as dropping a part number, since it changes what physical part is being
    discussed. Don't trust output that downgrades a specific part noun."""

    def setUp(self):
        ai_rewriter._client = None

    def test_fittings_downgraded_to_components_triggers_fallback(self):
        raw = "trying the fittings on the new drive motor from the old motor getting replaced"
        ai_out = "Attempted fitting of components from the new drive motor onto the old motor being replaced."
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(ai_out)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(raw)
        self.assertIn("fitting", result.lower())
        self.assertNotEqual(result, ai_out)

    def test_plural_singular_rewording_is_not_flagged(self):
        """Safety: comparing by stem means 'wire' -> 'wires' is fine."""
        dropped = ai_rewriter._dropped_part_nouns("replaced the bad wire", "Replaced the bad wires.")
        self.assertEqual(dropped, set())

    def test_fitting_used_as_verb_does_not_count_as_preserved(self):
        """False-friend guard: proven live, the model wrote 'Attempted fitting
        of components...' -- the bare stem 'fitting' is technically present,
        but it's the verb/gerund sense (the ACT of fitting), not the noun
        (the physical parts). Must still be flagged as dropped."""
        dropped = ai_rewriter._dropped_part_nouns(
            "trying the fittings on the new drive motor",
            "Attempted fitting of components on the new drive motor.",
        )
        self.assertIn("fitting", dropped)


class TestNewOldDirectionReversalGuard(unittest.TestCase):
    """Ground-truth finding: the model non-deterministically reverses which
    piece of equipment is the source vs. destination when 'new' and 'old'
    both appear (proven live on the same SCRUB0001 remark across different
    runs). This is dangerous specifically because it's inconsistent --
    sometimes right, sometimes backwards."""

    def setUp(self):
        ai_rewriter._client = None

    def test_detects_reversed_direction(self):
        raw = "trying the fittings on the new drive motor from the old motor getting replaced"
        ai_out = "Attempted fitting of components from the new drive motor onto the old motor being replaced."
        self.assertTrue(ai_rewriter._new_old_direction_reversed(raw, ai_out))

    def test_correct_direction_is_not_flagged(self):
        raw = "trying the fittings on the new drive motor from the old motor getting replaced"
        ai_out = "Attempted fitting of the new drive motor using components from the old motor being replaced."
        self.assertFalse(ai_rewriter._new_old_direction_reversed(raw, ai_out))

    def test_reversed_direction_triggers_fallback_in_rewrite(self):
        raw = "trying the fittings on the new drive motor from the old motor getting replaced"
        ai_out = "Attempted fitting of components from the new drive motor onto the old motor being replaced."
        client_mock = MagicMock()
        client_mock.is_closed = False
        client_mock.post.return_value = _make_ok_response(ai_out)
        with patch.object(ai_rewriter, "_get_client", return_value=client_mock):
            result = ai_rewriter.rewrite(raw)
        self.assertNotEqual(result, ai_out)

    def test_no_new_old_pattern_is_not_flagged(self):
        self.assertFalse(ai_rewriter._new_old_direction_reversed(
            "Replaced lower brushes on motors 9,5,4", "Replaced lower brushes on motors 9, 5, and 4."))


class TestContentFreeRemarkFilter(unittest.TestCase):
    """Ground-truth finding from real 9/15 timecard data: WO 27952416
    (TSP0002) already had a real substantive remark logged earlier the
    shift, then a later entry for the SAME WO just said "Assist" -- zero
    content, adds nothing. Distinction that matters: 'assisted' combined
    with a real part/action/location is fine; 'assisted' combined with only
    a generic activity word (troubleshooting, checking, etc.) is not."""

    def test_bare_assist_is_content_free(self):
        self.assertTrue(ai_rewriter._is_content_free_remark("Assist"))
        self.assertTrue(ai_rewriter._is_content_free_remark("assisted"))

    def test_assisted_in_troubleshooting_is_content_free(self):
        self.assertTrue(ai_rewriter._is_content_free_remark("Assisted in troubleshooting"))

    def test_assisted_with_specific_detail_is_not_content_free(self):
        self.assertFalse(ai_rewriter._is_content_free_remark(
            "Assisted with installing new brushes on the flex"))

    def test_checking_with_specific_nouns_is_not_content_free(self):
        """Real remark from earlier session data -- 'checking' is generic,
        but 'dashboard'/'wiring'/'fuse' are concrete, so this must survive."""
        self.assertFalse(ai_rewriter._is_content_free_remark(
            "Checking the dashboard for loose wiring or a bad fuse"))

    def test_drop_removes_content_free_remark_but_keeps_real_one(self):
        result = ai_rewriter._drop_content_free_remarks([
            "I got the gasket from TSP0008. Upon installing the stud screws I noticed two holes needed fixing.",
            "Assist",
        ])
        self.assertEqual(len(result), 1)
        self.assertNotIn("Assist", result)

    def test_drop_never_wipes_list_to_nothing(self):
        """Safety rail: if EVERY remark is content-free, keep them all --
        an empty bullet is worse than a vague one."""
        result = ai_rewriter._drop_content_free_remarks(["Assist", "Assisted in troubleshooting"])
        self.assertEqual(len(result), 2)

    def test_build_input_drops_content_free_remark(self):
        inp = ai_rewriter.build_input("TSP0002", [
            "I got the gasket from TSP0008. Upon installing the stud screws I noticed two holes needed fixing.",
            "Assist",
        ])
        self.assertNotIn(" | Assist", inp)


class TestVagueSubsetRemarkDedup(unittest.TestCase):
    """Ground-truth finding from real 9/15 data: TSP0008 / WO 27873128 had
    a detailed remark logged ("Gasket taken off from TSP0008 to be used on
    TSP0002. An order for a new gasket has already been placed") plus a
    separate, vaguer entry ("assist with removal of gasket") that said
    nothing new -- the overlap-ratio dedup missed it because only one word
    ("gasket") out of three overlapped, below the ratio threshold, even
    though that one word was the vague remark's ENTIRE concrete content."""

    def test_vague_removal_remark_collapses_into_detailed_one(self):
        result = ai_rewriter._dedupe_vague_subset_remarks([
            "assist with removal of gasket",
            "Gasket taken off from TSP0008 to be used on TSP0002. An order for a new gasket has already been placed",
        ])
        self.assertEqual(len(result), 1)
        self.assertIn("TSP0002", result[0])

    def test_unrelated_vague_remark_is_not_collapsed(self):
        """Real sibling remark from the same WO: 'Assist with removing info
        link' describes a DIFFERENT action -- must survive untouched."""
        result = ai_rewriter._dedupe_vague_subset_remarks([
            "Assist with removing info link",
            "Gasket taken off from TSP0008 to be used on TSP0002. An order for a new gasket has already been placed",
        ])
        self.assertEqual(len(result), 2)

    def test_never_collapses_a_remark_with_a_unique_number(self):
        result = ai_rewriter._dedupe_vague_subset_remarks([
            "checked fuse box 4",
            "Checked the fuse box thoroughly and confirmed everything looked fine and secure",
        ])
        self.assertEqual(len(result), 2)

    def test_never_wipes_list_to_nothing(self):
        result = ai_rewriter._dedupe_vague_subset_remarks(["remove gasket", "gasket removal done"])
        self.assertGreaterEqual(len(result), 1)

    def test_build_input_applies_subset_dedup(self):
        inp = ai_rewriter.build_input("TSP0008", [
            "Assist with removing info link",
            "assist with removal of gasket",
            "Gasket taken off from TSP0008 to be used on TSP0002. An order for a new gasket has already been placed",
        ])
        self.assertNotIn("removal of gasket", inp.lower())
        self.assertIn("info link", inp.lower())
        self.assertIn("tsp0002", inp.lower())


class TestLeadingVerbTenseFix(unittest.TestCase):
    """Ground-truth finding from real 9/15 S2 report: passdowns describe
    work that ALREADY happened, but checklist-style raw remarks like "Take
    meter reading on all Fork Lifts" and "Performing the function test"
    read as an instruction / an ongoing action -- both bypass the AI
    (short/checklist phrasing) and shipped present-tense, verbatim."""

    def test_take_becomes_took(self):
        self.assertEqual(
            ai_rewriter._fix_leading_tense("Take meter reading on all Fork Lifts."),
            "Took meter reading on all Fork Lifts.")

    def test_performing_becomes_performed_per_sentence(self):
        self.assertEqual(
            ai_rewriter._fix_leading_tense("Performing the function test. Plugging:5 ft Braking:3 ft"),
            "Performed the function test. Plugging:5 ft Braking:3 ft")

    def test_mid_sentence_verb_is_untouched(self):
        """Safety: only the leading word of each sentence is touched --
        mid-sentence verbs are far too ambiguous to rewrite blindly."""
        text = "The screw needs to check the alignment before install."
        self.assertEqual(ai_rewriter._fix_leading_tense(text), text)

    def test_build_input_applies_tense_fix(self):
        inp = ai_rewriter.build_input("BLDG", ["Take meter reading on all Fork Lifts."])
        self.assertIn("Took meter reading", inp)


class TestAssistedWithRemovingGap(unittest.TestCase):
    """Ground-truth finding from real 9/15 data: TSP0008 / WO 27873128's
    'Assist with removing info link' had no specific pattern for 'removing',
    so it fell through to the generic Assist-with->Performed rule, producing
    the awkward double-verb 'Performed removing info link' instead of the
    correct 'Removed info link'."""

    def test_assist_with_removing_becomes_removed(self):
        result = ai_rewriter._normalize_assisted_with("Assist with removing info link")
        self.assertEqual(result, "Removed info link")

    def test_build_input_fixes_removing_gap(self):
        inp = ai_rewriter.build_input("TSP0008", ["Assist with removing info link"])
        self.assertIn("Removed info link", inp)
        self.assertNotIn("Performed removing", inp)


if __name__ == "__main__":
    unittest.main()

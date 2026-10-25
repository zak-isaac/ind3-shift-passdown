"""Safety checks for IND3 automatic dictionary proofreading.

Uncertain or technical vocabulary MUST remain unchanged. These regressions
come from actual Cetaris remarks and previously observed miscorrections.
"""
import pytest

from spelling_fix import correct_spelling
from v3_proofread import proofread


@pytest.mark.parametrize("word", [
    "sensors", "rollers", "levelers", "chocks", "calibration", "chute",
    "solenoid", "tampad", "powercycled", "flapcut", "PLC", "SCADA",
    "VFD", "MDR", "RTS", "WO", "KNAPP", "Ranpak", "Cetaris",
    "bager0013", "BAG0007", "VOIDFILL0501", "WO28025279",
    "AREA0242RRM", "TSP0008", "M504", "M507", "10A", "6-2",
    "5:30", "o-ring", "unknowntechword"
])
def test_technical_words_identifiers_and_unknowns_are_preserved(word):
    assert correct_spelling(word) == word


@pytest.mark.parametrize(("original", "expected"), [
    ("colum", "column"),
    ("tention", "tension"),
    ("adressed", "addressed"),
    ("Frist", "First"),
    ("sstep", "step"),
    ("intstalled", "installed"),
    ("checked colum tention", "checked column tension"),
])
def test_only_reviewed_typos_are_corrected(original, expected):
    assert correct_spelling(original) == expected


@pytest.mark.parametrize("text", [
    "Bale alignment and sensors free of obstructions",
    "Adjusted label position sensor back to prevent tampad from catching label",
    "Paper jammed in chute, cleared and watched run a few min",
    "Replaced FSM controller on BAG0007",
    "Found loose o-ring on the solenoid; tested PLC; RTS",
    "Please inspect bager0013, not BAG0013",
    "calibration and rollers checked on VOIDFILL0501",
])
def test_real_cetaris_phrases_do_not_drift(text):
    assert correct_spelling(text) == text


def test_automatic_proofreader_corrects_known_typos_without_guessing():
    raw = "checked colum tention on VOIDFILL0501;PLC RTS"
    cleaned = proofread(raw)
    assert cleaned == "Checked column tension on VOIDFILL0501; PLC RTS"
    assert proofread(cleaned) == cleaned


def test_proofreader_preserves_technical_meaning_and_model_ids():
    original = "bale alignment and sensors free of obstructions on BAG0007"
    assert proofread(original) == "Bale alignment and sensors free of obstructions on BAG0007"
    assert proofread("replaced FSM controller on BAG0007") == "Replaced FSM controller on BAG0007"

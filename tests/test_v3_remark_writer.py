"""Acceptance tests for deterministic maintenance writer."""
from v3_remark_writer import rewrite_wo

def test_cr0009_two_labor_lines_reordered_without_losses():
    draft = rewrite_wo(["blue light 5ft\nbraking 3ft\nplugging 5ft",
                        "changed both  right and left load wheels"])
    assert draft.mode == "rule:wheels-and-measurements"
    assert draft.text == ("Replaced both the left and right load wheels. "
                          "Recorded the blue light at 5 ft, braking at 3 ft, and plugging at 5 ft.")
    assert not draft.requires_review

def test_bag0002_semantic_duplicate():
    d = rewrite_wo(["Paper adjusted", "Paper adjustment."])
    assert d.text == "Adjusted the paper."
    assert not d.requires_review

def test_two_tech_identical_notes_not_repeated():
    d = rewrite_wo(["Motor Brush clean up", "Motor Brush clean up"])
    assert d.text == "Cleaned the motor brush."

def test_complex_wo_preserves_distinct_details():
    remarks = ["Code 222 overheat. Reflashed software.",
               "Called Crown support. Braking 6ft plugging 10ft"]
    d = rewrite_wo(remarks)
    assert d.requires_review
    assert all(s in d.text for s in ("Code 222", "Reflashed", "Crown", "6ft", "10ft"))

def test_rts_oos_not_interpreted_as_global_rts():
    d = rewrite_wo(["Motor still OOS", "Gate RTS"])
    assert d.requires_review
    assert d.text == "Motor still OOS; Gate RTS"

def test_unknown_additional_technical_detail_remains():
    d = rewrite_wo(["Changed both right and left load wheels",
                    "Blue light 5ft braking 3ft plugging 5ft; discovered oil leak"])
    assert d.requires_review
    assert "oil leak" in d.text

def test_missing_remarks():
    d = rewrite_wo(["", "  "])
    assert d.requires_review
    assert d.text == "No technician remarks recorded."

def test_same_tech_separate_labor_lines_supported():
    d = rewrite_wo(["changed both left and right load wheels",
                    "blue light 5ft braking 3ft plugging 5ft"])
    assert d.mode == "rule:wheels-and-measurements"
    assert "3 ft" in d.text


def test_alias_equivalence_across_two_techs():
    d = rewrite_wo(["Motor Brush clean up", "Cleaned the motor brush."])
    assert d.text == "Cleaned the motor brush."
    assert not d.requires_review


def test_paraphrased_paper_adjustment():
    d = rewrite_wo(["paper adjustment", "adjusted the paper"])
    assert d.text == "Adjusted the paper."
    assert not d.requires_review


def test_same_action_but_distinct_extra_repair_kept():
    d = rewrite_wo(["Paper adjusted", "adjusted the paper", "Replaced roller on BAG0002"])
    assert "Adjusted the paper." in d.text
    assert "Replaced roller on BAG0002" in d.text
    assert d.requires_review


def test_direction_and_measurement_not_semantically_collapsed():
    d = rewrite_wo(["Cleaned left motor brush", "Cleaned right motor brush",
                    "Braking 6ft", "Plugging 10ft"])
    assert d.requires_review
    assert all(term in d.text for term in ["left", "right", "6ft", "10ft"])


def test_status_not_inferred_from_equivalent_action():
    d = rewrite_wo(["Motor Brush clean up", "Cleaned the motor brush"])
    assert "RTS" not in d.text
    assert "replaced" not in d.text.lower()

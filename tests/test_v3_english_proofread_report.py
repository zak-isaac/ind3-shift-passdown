"""Spelling + AI acceptance from real IND3 S4/S5 problem patterns.

All identifiers and people in fixtures are synthetic. Verify BOTH fields use
the same correctable text in workspace and generated report, including when
Ollama validates a rewrite or safely restores originals.
"""
import json

import spelling_fix
from v3_proofread import proofread
from v3_report_layout import build_shift_happenings, heading, remark
import v3_ollama_background as ai


def _entry(raw, issue="griper not griping", wo="TEST-ENGLISH"):
    return {"id":1,"wo_number":wo,"unit":"BAG0002","issue":issue,
            "raw_remarks":json.dumps([raw]),"source":"cmms",
            "action":raw,"excluded":0}


def test_normal_english_typos_in_cause_and_tech_remarks():
    assert proofread("griper not griping, boixes toren") == (
        "Gripper not gripping, boxes torn")
    assert proofread("paper got raped around roller then i ran it throught unit") == (
        "Paper got wrapped around roller then I ran it through unit")
    assert proofread("waiting to get produck; haveing more issues") == (
        "Waiting to get product; having more issues")


def test_wo_codes_acronyms_technical_terms_never_changed():
    text="MDR0016 PLC HMI VOIDFILL0201 O-Ring FSM TSP0005 VFD"
    assert spelling_fix.correct_spelling(text)==text
    assert spelling_fix.correct_spelling("Crown Autocar servo taper") == (
        "Crown Autocar servo taper")


def test_fallback_report_corrects_both_cause_and_remarks():
    e=_entry("paper was toren, i checked the griper and replaced servor",
             issue="griper not griping while boixes move")
    happenings=build_shift_happenings([e],{1:{"cause_text":e["issue"]}})
    assert len(happenings)==1
    h=happenings[0]
    assert heading(h)=="BAG0002: Gripper not gripping while boxes move (WO#TEST-ENGLISH)"
    assert remark(h)=="Paper was torn, I checked the gripper and replaced servo"


def test_ai_passed_paragraph_is_also_english_proofread():
    e=_entry("paper toren and griper not griping")
    h=build_shift_happenings([e],{},approved_remarks={
        "TEST-ENGLISH":"Changed servor after paper toren."})[0]
    assert remark(h)=="Changed servo after paper torn."
    assert heading(h)=="BAG0002: Gripper not gripping (WO#TEST-ENGLISH)"


def test_general_dictionary_engine_handles_unlisted_english_typos(monkeypatch):
    """Not only the reviewed override map is considered for normal English."""
    class FakeDictionary:
        word_frequency={"calibration":500,"collaboration":5}
        def known(self,words):return set()
        def candidates(self,word):
            if word=="calibraton":return {"calibration","collaboration"}
            return set()
    monkeypatch.setattr(spelling_fix,"_spell",FakeDictionary())
    assert spelling_fix.correct_spelling("Checked calibraton") == (
        "Checked calibration")


def test_valid_technician_result_without_exact_same_words_is_accepted():
    src=["The boxes were crushed by the taper head left flap, tower false reading, "
         "I recalibrated it and things are back to normal."]
    result=("Boxes were crushed by the taper head left flap due to the tower's "
            "false reading. Recalibrated the tower and operation returned to normal.")
    okay,reason=ai._verify(src,result)
    assert okay,reason


def test_successful_case_count_can_be_paraphrased_without_inventing_result():
    src=["I assisted with tape change, adjusted tension and blade position, "
         "replaced the blade and was able to watch 50 cases run without issue."]
    draft=("Assisted with tape change, adjusted tension and blade position, "
           "replaced the blade, and watched 50 cases run without issue.")
    okay,reason=ai._verify(src,draft)
    assert okay,reason


def test_technician_short_assist_note_survives_ai_rewrite():
    source=[
        "Troubleshot an air leak when the supply pushbutton was engaged, "
        "removed and replaced the air brake control valve. RTS.",
        "Assist"]
    proposal=("Troubleshot the air leak when the supply pushbutton was engaged. "
              "Removed and replaced the air brake control valve. RTS.")
    enriched=ai._ensure_short_contributions(source,proposal)
    assert enriched.endswith("; Assisted.")
    ok,reason=ai._verify(source,enriched)
    assert ok,reason


def test_ai_cannot_invent_new_component_or_repair():
    ok,reason=ai._verify(["Cleaned taper blade and reset module."],
                         "Cleaned taper blade, reset module and replaced fuse.")
    assert not ok and "unsupported" in reason.lower()


def test_ai_cannot_erase_an_independent_long_second_technician_note():
    ok,reason=ai._verify([
        "Replaced printer controller and tested power.",
        "Reset HMI; fault remained, bagger was locked out."],
        "Replaced printer controller and tested power.")
    assert not ok and "source remark" in reason.lower()


def test_ai_cannot_invent_or_drop_technical_numbers():
    assert not ai._verify(["Recalibrated fill head from 760 to 784"],
                          "Recalibrated fill head from 760 to 790")[0]
    assert not ai._verify(["Measured 110 psi and monitored."],
                          "Measured 110 psi and 120 psi and monitored.")[0]


def test_ai_does_not_invent_rts_from_closed_status():
    assert not ai._verify(["Replaced valve. Monitoring ongoing."],
                          "Replaced valve. Monitoring ongoing. RTS.")[0]

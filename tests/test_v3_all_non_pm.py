"""All closed non-PM RFRs must be retained, including unknown categories."""
from datetime import datetime
from v3_cetaris import select_records

def test_all_non_pm_categories_and_unknown_are_happenings():
    base=dict(tech_name="Tech A",asset_id="A1",status="Closed",reported_issue="Reported",
              cause="",remarks="Done",start_at="2026-10-20T14:00:00",closed_at="",
              repair_class="Unplanned",pm_code="",last_shift="S2")
    categories=["Breakdown","Process Interruption","Proactive Repair","Work Request","Inspection","Corrective Maintenance","Other",""]
    rows=[{**base,"wo_number":str(100+i),"rfr":category} for i,category in enumerate(categories)]
    # Real PM rows are identified by a populated SJ / PM Code, not by RFR text.
    rows += [{**base,"wo_number":str(200+i),"rfr":"Preventive Maintenance","pm_code":"PACK.007.PKM"} for i in range(3)]
    rows += [{**base,"wo_number":"900","rfr":"Inspection","status":"Open"}]
    out,summary=select_records(rows,{"Tech A"},datetime(2026,10,20,12),datetime(2026,10,20,22))
    happenings=[r for r in out if r["kind"]=="happening"]
    pms=[r for r in out if r["kind"]=="pm"]
    assert len(happenings)==8
    assert len(pms)==3
    assert summary["not_closed"]==1
    assert {r["rfr"] for r in happenings}==set(categories)
    assert "unsupported_rfr" not in summary


def test_pm_detected_via_sj_pm_code_even_with_unrelated_rfr():
    """PM status comes primarily from a populated SJ / PM Code -- not from
    Primary RFR or any other reason-for-repair text. A WO whose RFR is some
    ordinary repair reason but whose SJ / PM Code is populated must still
    count as a PM, and a WO with no SJ / PM Code must never count as one
    even if its RFR happens to mention maintenance."""
    base = dict(tech_name="Tech A", asset_id="A1", status="Closed", reported_issue="Reported",
                cause="", remarks="Done", start_at="2026-10-20T14:00:00", closed_at="",
                repair_class="Planned", last_shift="S2", rfr="Scheduled Service")
    rows = [
        {**base, "wo_number": "1", "pm_code": "PACK.007.PKM"},
        {**base, "wo_number": "2", "pm_code": "E.001.BALER.WALK"},
        {**base, "wo_number": "3", "pm_code": ""},
    ]
    out, summary = select_records(rows, {"Tech A"}, datetime(2026, 10, 20, 12), datetime(2026, 10, 20, 22))
    pms = [r for r in out if r["kind"] == "pm"]
    happenings = [r for r in out if r["kind"] == "happening"]
    assert {r["wo_number"] for r in pms} == {"1", "2"}
    assert {r["wo_number"] for r in happenings} == {"3"}


def test_duplicate_pm_wo_number_counts_once():
    """WO# 28023560-style case: the same PM WO logged twice (two remarks /
    two labor rows) must count as ONE completed PM, not two."""
    base = dict(tech_name="Tech A", asset_id="A1", status="Closed", reported_issue="Baler Walk",
                cause="", start_at="2026-10-20T14:00:00", closed_at="",
                repair_class="Planned", last_shift="S2", rfr="Preventive Maintenance",
                pm_code="E.001.BALER.WALK", wo_number="28023560")
    rows = [{**base, "remarks": "Horizontal pass"}, {**base, "remarks": "Perimeter check"}]
    out, summary = select_records(rows, {"Tech A"}, datetime(2026, 10, 20, 12), datetime(2026, 10, 20, 22))
    assert summary["unique_pm_wos"] == 1
    assert len(out) == 1
    assert set(out[0]["remarks_list"]) == {"Horizontal pass", "Perimeter check"}

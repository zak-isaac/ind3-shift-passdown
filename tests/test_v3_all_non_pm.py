"""All closed non-PM RFRs must be retained, including unknown categories."""
from datetime import datetime
from v3_cetaris import select_records

def test_all_non_pm_categories_and_unknown_are_happenings():
    base=dict(tech_name="Tech A",asset_id="A1",status="Closed",reported_issue="Reported",cause="",remarks="Done",start_at="2026-10-20T14:00:00")
    categories=["Breakdown","Process Interruption","Proactive Repair","Work Request","Inspection","Corrective Maintenance","Other","", "Preventive Maintenance", "PM", "Preventative Maintenance"]
    rows=[{**base,"wo_number":str(100+i),"rfr":category} for i,category in enumerate(categories)]
    rows += [{**base,"wo_number":"900","rfr":"Inspection","status":"Open"}]
    out,summary=select_records(rows,{"Tech A"},datetime(2026,10,20,12),datetime(2026,10,20,22))
    happenings=[r for r in out if r["kind"]=="happening"]
    pms=[r for r in out if r["kind"]=="pm"]
    assert len(happenings)==9
    assert len(pms)==2
    assert summary["not_closed"]==1
    assert {r["rfr"] for r in happenings}==set(categories[:10]) - {"Preventive Maintenance"}
    assert "unsupported_rfr" not in summary


def test_pm_detected_via_sj_pm_code_even_with_unrelated_rfr():
    """2026-10-26: real facility pm_uploads/*.csv exports confirmed a
    populated SJ / PM Code (e.g. 'RNPK.001.PKM', 'E.001.BALER.WALK') always
    co-occurs with Primary RFR == 'Preventive Maintenance' -- but Cetaris
    technically allows any RFR text on a PM-coded WO. A WO whose RFR is an
    ordinary repair reason but whose SJ / PM Code is populated must still
    count as a PM, and a WO with no SJ / PM Code must never count as one
    just because its RFR happens to mention maintenance in passing."""
    base = dict(tech_name="Tech A", asset_id="A1", status="Closed", reported_issue="Reported",
                cause="", remarks="Done", start_at="2026-10-20T14:00:00", rfr="Scheduled Service")
    rows = [
        {**base, "wo_number": "1", "pm_code": "RNPK.001.PKM"},
        {**base, "wo_number": "2", "pm_code": "E.001.BALER.WALK"},
        {**base, "wo_number": "3", "pm_code": ""},
    ]
    out, summary = select_records(rows, {"Tech A"}, datetime(2026, 10, 20, 12), datetime(2026, 10, 20, 22))
    pms = [r for r in out if r["kind"] == "pm"]
    happenings = [r for r in out if r["kind"] == "happening"]
    assert {r["wo_number"] for r in pms} == {"1", "2"}
    assert {r["wo_number"] for r in happenings} == {"3"}


def test_pm_rfr_still_counts_as_fallback_when_pm_code_blank():
    """Blank SJ / PM Code must not silently stop a currently-recognized
    Primary RFR == Preventive Maintenance WO from counting -- the PM Code
    check is a stronger signal when present, not a replacement that drops
    coverage when the field is empty."""
    base = dict(tech_name="Tech A", asset_id="A1", status="Closed", reported_issue="Reported",
                cause="", remarks="Done", start_at="2026-10-20T14:00:00",
                rfr="Preventive Maintenance", pm_code="")
    rows = [{**base, "wo_number": "500"}]
    out, summary = select_records(rows, {"Tech A"}, datetime(2026, 10, 20, 12), datetime(2026, 10, 20, 22))
    assert {r["wo_number"] for r in out if r["kind"] == "pm"} == {"500"}


def test_duplicate_pm_wo_number_counts_once_real_pm_code():
    """WO# 28023560-style case using a real observed facility PM code
    (E.001.BALER.WALK, confirmed in pm_uploads/*.csv): the same PM WO
    logged twice (two remarks / two labor rows) must count as ONE
    completed PM, not two."""
    base = dict(tech_name="Tech A", asset_id="A1", status="Closed", reported_issue="Baler Walk",
                cause="", start_at="2026-10-20T14:00:00", rfr="Preventive Maintenance",
                pm_code="E.001.BALER.WALK", wo_number="28023560")
    rows = [{**base, "remarks": "Horizontal pass"}, {**base, "remarks": "Perimeter check"}]
    out, summary = select_records(rows, {"Tech A"}, datetime(2026, 10, 20, 12), datetime(2026, 10, 20, 22))
    assert summary["unique_pm_wos"] == 1
    assert len(out) == 1
    assert set(out[0]["remarks_list"]) == {"Horizontal pass", "Perimeter check"}

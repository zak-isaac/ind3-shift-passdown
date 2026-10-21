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
    assert len(happenings)==8
    assert len(pms)==3
    assert summary["not_closed"]==1
    assert {r["rfr"] for r in happenings}==set(categories[:8])
    assert "unsupported_rfr" not in summary

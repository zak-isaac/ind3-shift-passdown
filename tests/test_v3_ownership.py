"""Ownership planner regression tests: run with pytest -q tests/test_v3_ownership.py."""
from datetime import datetime as D
from v3_ownership import plan_work_orders

def row(wo, tech, status="Completed", cat="Breakdown", complete="2026-10-22T22:00:00", closed="2026-10-23T01:00:00"):
    return {"wo_number":wo,"tech_name":tech,"status":status,"rfr":cat,"last_complete_at":complete,"closed_at":closed,"asset_id":"VOIDFILL0901","remarks":"Fixed"}

def plan(rows,shift="S2",roster=None,saved=None):
    return plan_work_orders(rows,{r["tech_name"] for r in rows},shift,D(2026,10,22,12),D(2026,10,22,22,30),roster,saved)[0]

def test_pm_completed_counts_automatically():
    assert plan([row("1","Isaac",cat="Preventive Maintenance")])["action"]=="auto"

def test_completed_non_pm_requires_verification():
    assert plan([row("1","Isaac")])["action"]=="verification"

def test_manager_late_close_does_not_change_shift():
    assert plan([row("1","Isaac",status="Closed",closed="2026-10-23T04:00:00")])["action"]=="auto"

def test_overtime_original_shift_needs_ownership():
    assert plan([row("1","Isaac",complete="2026-10-22T23:15:00")])["action"]=="ownership"

def test_assisting_other_shift_needs_ownership():
    assert plan([row("1","Isaac"),row("1","Sam")],roster={"Isaac":"S2","Sam":"S3"})["action"]=="ownership"

def test_confirmed_original_shift_included():
    assert plan([row("1","Isaac",complete="2026-10-22T23:15:00")],saved={"1":{"decision":"include","owner_shift":"S2"}})["action"]=="auto"

def test_other_shift_does_not_receive_credit():
    assert plan([row("1","Isaac")],saved={"1":{"decision":"include","owner_shift":"S3"}})["action"]=="other_shift"

def test_missing_completion_time_needs_review():
    assert plan([row("1","Isaac",complete="")])["action"]=="ownership"

def test_in_process_breakdown_tracks_oos():
    assert plan([row("1","Isaac",status="In Process")])["action"]=="oos"

def test_deduplicates_technician_rows():
    p=plan([row("1","Isaac"),row("1","Isaac")]); assert len(p["techs"])==1 and len(p["remarks"])==1

def test_defer_does_not_credit():
    assert plan([row("1","Isaac")],saved={"1":{"decision":"defer","owner_shift":""}})["action"]=="pending"

def test_s3_overnight_completion_belongs_to_evening_start_shift():
    rows=[row("900","Sam",status="Closed",cat="Preventive Maintenance",
              complete="2026-10-23T03:30:00",closed="2026-10-23T10:00:00")]
    planned=plan_work_orders(rows,{"Sam"},"S3",D(2026,10,22,20,30),
                            D(2026,10,23,7),{"Sam":"S3"})
    assert planned[0]["action"]=="auto"

def test_s5_overnight_completion_belongs_to_evening_start_shift():
    rows=[row("901","Sam",status="Completed",cat="Preventive Maintenance",
              complete="2026-10-24T02:30:00",closed="2026-10-24T10:00:00")]
    planned=plan_work_orders(rows,{"Sam"},"S5",D(2026,10,23,17,30),
                            D(2026,10,24,6),{"Sam":"S5"})
    assert planned[0]["action"]=="auto"

def test_cetaris_last_first_name_matches_roster_first_last():
    assert plan([row("1","Ssaku, Isaac")],roster={"Isaac Ssaku":"S2"})["action"]=="verification"

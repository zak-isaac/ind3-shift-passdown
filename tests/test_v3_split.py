"""S4/S5 ownership regression tests: work time beats administrative closure."""
from v3_split import assign, owner_for_close


def row(wo="101", tech="Tech A", start="", service="", opened="", closed="", status="Closed"):
    return {"wo_number":wo,"tech_name":tech,"status":status,"asset_id":"M1",
            "start_at":start,"in_service_at":service,"opened_at":opened,
            "closed_at":closed,"last_complete_at":""}


def test_overlap_s4_and_s5_boundary():
    d="2026-10-23"
    assert owner_for_close("2026-10-23T17:45:00",d)=="S4"
    assert owner_for_close("2026-10-23T18:00:00",d)=="S5"


def test_late_admin_close_does_not_steal_s4_work():
    rows=[row(start="2026-10-23T17:17:00",service="2026-10-23T17:43:00",
              opened="2026-10-23T15:50:00",closed="2026-10-24T06:15:00")]
    assert assign(rows,{"Tech A"},"S4","2026-10-23")==(rows,[])
    assert assign(rows,{"Tech A"},"S5","2026-10-23",confirmed_s5_techs={"Tech A"})==([],[])


def test_s5_real_work_overrides_earlier_open():
    rows=[row(start="2026-10-23T20:00:00",service="2026-10-23T20:20:00",
              opened="2026-10-23T17:45:00")]
    assert assign(rows,{"Tech A"},"S5","2026-10-23")==(rows,[])


def test_cross_shift_work_requires_review():
    rows=[row(start="2026-10-23T17:45:00",service="2026-10-23T18:15:00")]
    accepted,pending=assign(rows,{"Tech A"},"S5","2026-10-23")
    assert not accepted and len(pending)==1
    assert assign(rows,{"Tech A"},"S5","2026-10-23",{"101":"S5"})==(rows,[])


def test_s5_grace_requires_confirmation_for_actual_work():
    rows=[row(start="2026-10-24T06:15:00",service="2026-10-24T06:20:00")]
    assert assign(rows,{"Tech A"},"S5","2026-10-23")[1]
    assert assign(rows,{"Tech A"},"S5","2026-10-23",confirmed_s5_techs={"Tech A"})==(rows,[])


def test_in_service_fallback_without_start():
    rows=[row(service="2026-10-23T22:10:00",opened="2026-10-23T21:50:00")]
    assert assign(rows,{"Tech A"},"S5","2026-10-23")==(rows,[])


def test_open_only_requires_review():
    rows=[row(opened="2026-10-23T17:00:00",closed="2026-10-23T17:30:00")]
    assert assign(rows,{"Tech A"},"S4","2026-10-23")[1]


def test_multi_tech_same_shift_single_wo():
    rows=[row(tech="Tech A",start="2026-10-23T20:00:00",service="2026-10-23T20:30:00"),
          row(tech="Tech B",start="2026-10-23T20:05:00",service="2026-10-23T20:30:00")]
    assert assign(rows,{"Tech A","Tech B"},"S5","2026-10-23")==(rows,[])


def test_weekday_s2_is_filtered_by_its_shift_date():
    rows=[row(start="2026-10-22T18:00:00",service="2026-10-22T18:30:00")]
    assert assign(rows,{"Tech A"},"S2","2026-10-22")==(rows,[])
    assert assign(rows,{"Tech A"},"S1","2026-10-22")==([],[])


def test_in_process_oos_is_shift_specific():
    rows=[row(status="In process",start="2026-10-23T21:00:00")]
    assert assign(rows,{"Tech A"},"S5","2026-10-23")==(rows,[])
    assert assign(rows,{"Tech A"},"S4","2026-10-23")==([],[])


def test_thursday_s3_continues_friday_morning_with_roster():
    rows=[row(wo="28025279",tech="Stamps, Ray",start="2026-10-23T05:55:00",
              service="2026-10-23T06:53:00")]
    roster={"Stamps, Ray":"S3"}
    assert assign(rows,{"Stamps, Ray"},"S3","2026-10-22",roster_shifts=roster)==(rows,[])
    assert assign(rows,{"Stamps, Ray"},"S4","2026-10-23",roster_shifts=roster)==([],[])


def test_weekday_s1_s2_overlap_respects_roster():
    rows=[row(start="2026-10-22T13:00:00",service="2026-10-22T13:20:00")]
    assert assign(rows,{"Tech A"},"S1","2026-10-22",roster_shifts={"Tech A":"S1"})==(rows,[])
    assert assign(rows,{"Tech A"},"S2","2026-10-22",roster_shifts={"Tech A":"S1"})==([],[])


def test_no_timestamps_prompts_review():
    rows=[row()]
    assert assign(rows,{"Tech A"},"S2","2026-10-22")==([],[
        {"wo_number":"101","asset_id":"M1","closed_at":"",
         "time_source":"Technician Start / In Service / Roster",
         "technicians":"Tech A",
         "reason":"Missing or conflicting work-start and in-service evidence"}])


def test_s5_new_calendar_day_labor_is_owned_by_previous_evening():
    # Saturday S5 continues into Sunday. 00:45 is NOT a new Sunday S5.
    rows=[row(wo="AFTER-MIDNIGHT",start="2026-10-25T00:45:00",
              service="2026-10-25T01:10:00")]
    assert assign(rows,{"Tech A"},"S5","2026-10-24") == (rows,[])
    assert assign(rows,{"Tech A"},"S5","2026-10-25") == ([],[])


def test_s5_new_day_uses_last_complete_when_start_and_service_missing():
    rows=[{**row(wo="MIDNIGHT-PM"),"last_complete_at":"2026-10-25T01:15:00"}]
    assert assign(rows,{"Tech A"},"S5","2026-10-24") == (rows,[])


def test_s5_work_started_days_earlier_does_not_disappear_at_midnight():
    # Older Cetaris Start Date and WO opened date must not mask the work
    # completed during Saturday S5 when In Service is blank.
    rows=[{**row(wo="OLDER-OPEN",start="2026-10-22T14:00:00",
                 opened="2026-10-22T13:00:00"),
           "last_complete_at":"2026-10-25T01:45:00"}]
    accepted,pending=assign(rows,{"Tech A"},"S5","2026-10-24")
    assert accepted == []
    assert len(pending)==1 and pending[0]["wo_number"]=="OLDER-OPEN"
    assert "review" in pending[0]["reason"].lower()
    assert assign(rows,{"Tech A"},"S5","2026-10-24",{"OLDER-OPEN":"S5"}) == (rows,[])


def test_s5_record_after_midnight_does_not_count_for_prior_s4():
    rows=[{**row(wo="S5-ONLY",start="2026-10-25T03:00:00"),
           "last_complete_at":"2026-10-25T03:15:00"}]
    assert assign(rows,{"Tech A"},"S4","2026-10-24")==([],[])
    assert assign(rows,{"Tech A"},"S5","2026-10-24")==(rows,[])


def test_s5_sunday_evening_continues_into_monday_morning():
    rows=[row(wo="SUNDAY-TO-MONDAY",start="2026-10-26T03:10:00",
              service="2026-10-26T03:40:00")]
    assert assign(rows,{"Tech A"},"S5","2026-10-25")==(rows,[])
    assert assign(rows,{"Tech A"},"S5","2026-10-24")==([],[])

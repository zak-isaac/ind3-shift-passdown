"""Evidence-based WO ownership. All examples are synthetic."""
from v3_split import assign
from v3_import_audit import build_audit


def labor(wo, tech, start, service="", last_complete="", completed_by="",
          closed="", status="Closed", asset="BALER0003"):
    return {
        "wo_number":wo, "asset_id":asset, "tech_name":tech,
        "status":status,"rfr":"Proactive Repair","remarks":"Checked device",
        "reported_issue":"Inspection","start_at":start,
        "in_service_at":service,"last_complete_at":last_complete,
        "completed_by":completed_by,"closed_at":closed,"opened_at":"",
    }


def test_admin_completer_does_not_take_other_technicians_wo():
    # Historical labor and In Service precede the S5 handover.
    # The administrator's 18:39 completion/closure is not S5 work.
    r=labor("ADMIN-PM","Technician A","2026-10-23T03:02:00",
            service="2026-10-22T05:17:00",
            last_complete="2026-10-23T18:39:00",
            completed_by="Manager B",closed="2026-10-23T18:39:00")
    accepted,not_owned=assign([r],{"Technician A"},"S5","2026-10-23")
    assert accepted == []
    assert len(not_owned)==1
    audit=build_audit([r],{"Technician A"},accepted,[],
                      shift_type="S5",shift_day="2026-10-23",unresolved_wos=not_owned)
    assert audit["ignored_count"]==1
    assert "No verified technician completion" in audit["ignored"][0]["reason"]


def test_actual_completed_by_and_last_complete_transfer_to_s5_without_dialog():
    r=labor("OLD-START","Technician A","2026-10-22T14:00:00",
            last_complete="2026-10-24T01:45:00",
            completed_by="Technician A",closed="2026-10-25T11:00:00")
    assert assign([r],{"Technician A"},"S5","2026-10-23")==([r],[])
    assert assign([r],{"Technician A"},"S4","2026-10-23")==([],[])


def test_last_complete_without_identifiable_finisher_is_not_proof():
    r=labor("NO-FINISHER","Technician A","2026-10-23T20:00:00",
            last_complete="2026-10-23T21:00:00",
            closed="2026-10-24T06:10:00")
    accepted,not_owned=assign([r],{"Technician A"},"S5","2026-10-23")
    assert accepted==[] and len(not_owned)==1


def test_multi_tech_work_owned_by_finisher_without_duplicating_shift_credit():
    rows=[
        labor("TWO-TECHS","Technician A","2026-10-23T17:10:00",
              service="2026-10-23T18:26:00",
              last_complete="2026-10-23T18:27:00",completed_by="Technician B"),
        labor("TWO-TECHS","Technician B","2026-10-23T18:05:00",
              service="2026-10-23T18:26:00",
              last_complete="2026-10-23T18:27:00",completed_by="Technician B"),
    ]
    assert assign(rows,{"Technician A","Technician B"},"S5","2026-10-23")== (rows,[])
    assert assign(rows,{"Technician A","Technician B"},"S4","2026-10-23")== ([],[])


def test_equipment_return_beats_delayed_wo_administrative_close():
    r=labor("LATE-CLOSE","Technician A","2026-10-23T17:10:00",
            service="2026-10-23T17:42:00",
            last_complete="2026-10-23T19:05:00",
            completed_by="Manager B",closed="2026-10-23T19:05:00")
    assert assign([r],{"Technician A"},"S4","2026-10-23")==([r],[])
    assert assign([r],{"Technician A"},"S5","2026-10-23")==([],[])


def test_s3_joint_finish_at_0630_remains_prior_evenings_s3():
    rows=[
        labor("MORNING","S3 Technician","2026-10-22T23:10:00",
              service="2026-10-23T06:30:00",completed_by="S4 Technician"),
        labor("MORNING","S4 Technician","2026-10-23T05:50:00",
              service="2026-10-23T06:30:00",completed_by="S4 Technician"),
    ]
    assert assign(rows,{"S3 Technician","S4 Technician"},"S3","2026-10-22")== (rows,[])
    assert assign(rows,{"S3 Technician","S4 Technician"},"S4","2026-10-23")== ([],[])


def test_s5_sunday_to_monday_0430_and_not_evening_close():
    r=labor("OVERNIGHT","Night Technician","2026-10-26T04:05:00",
            service="2026-10-26T04:30:00",
            last_complete="2026-10-26T04:30:00",
            completed_by="Night Technician",closed="2026-10-26T11:00:00")
    assert assign([r],{"Night Technician"},"S5","2026-10-25")==([r],[])
    assert assign([r],{"Night Technician"},"S5","2026-10-24")==([],[])


def test_missing_ownership_does_not_block_other_verified_wos():
    bad=labor("BAD","Tech A","2026-10-23T18:05:00",
              last_complete="2026-10-23T19:00:00",completed_by="Supervisor B")
    good=labor("GOOD","Tech B","2026-10-23T18:10:00",
               service="2026-10-23T18:30:00",completed_by="Tech B")
    accepted,audit=assign([bad,good],{"Tech A","Tech B"},"S5","2026-10-23")
    assert accepted==[good]
    assert len(audit)==1 and audit[0]["wo_number"]=="BAD"

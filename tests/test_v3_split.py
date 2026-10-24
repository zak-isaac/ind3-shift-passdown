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


def test_nonsplit_unchanged():
    rows=[row()]
    assert assign(rows,{"Tech A"},"S2","2026-10-23")==(rows,[])


def test_in_process_preserved_for_oos():
    rows=[row(status="In process")]
    assert assign(rows,{"Tech A"},"S5","2026-10-23")==(rows,[])

"""S4/S5 ownership and grace-period regression tests."""
from v3_split import assign, owner_for_close


def row(wo="101", tech="Tech A", status="Closed", closed="", in_service=""):
    return {"wo_number":wo,"tech_name":tech,"status":status,"closed_at":closed,
            "in_service_at":in_service,"asset_id":"M1"}


def test_s4_owns_overlap_and_s5_starts_at_1800():
    d="2026-10-23"
    assert owner_for_close("2026-10-23T17:30:00",d)=="S4"
    assert owner_for_close("2026-10-23T17:45:00",d)=="S4"
    assert owner_for_close("2026-10-23T17:59:59",d)=="S4"
    assert owner_for_close("2026-10-23T18:00:00",d)=="S5"
    assert owner_for_close("2026-10-24T05:59:59",d)=="S5"


def test_grace_requires_confirmed_s5_technician():
    d="2026-10-23"
    assert owner_for_close("2026-10-24T06:00:00",d) is None
    assert owner_for_close("2026-10-24T06:15:00",d,confirmed_s5=True)=="S5"
    assert owner_for_close("2026-10-24T06:30:00",d,confirmed_s5=True)=="S5"
    assert owner_for_close("2026-10-24T06:30:01",d,confirmed_s5=True) is None


def test_overlap_not_credited_to_s5():
    rows=[row(closed="2026-10-23T17:43:00")]
    assert assign(rows,{"Tech A"},"S4","2026-10-23")==(rows,[])
    assert assign(rows,{"Tech A"},"S5","2026-10-23",confirmed_s5_techs={"Tech A"})==([],[])


def test_in_service_fallback():
    rows=[row(in_service="2026-10-23T22:10:00")]
    assert assign(rows,{"Tech A"},"S5","2026-10-23")==(rows,[])
    assert assign(rows,{"Tech A"},"S4","2026-10-23")==([],[])


def test_missing_timestamp_needs_review():
    rows=[row()]
    accepted,pending=assign(rows,{"Tech A"},"S5","2026-10-23")
    assert accepted==[] and pending[0]["time_source"]=="Missing timestamp"
    assert assign(rows,{"Tech A"},"S5","2026-10-23",{"101":"S5"})==(rows,[])


def test_s5_grace_with_confirmed_tech():
    rows=[row(closed="2026-10-24T06:15:00")]
    assert assign(rows,{"Tech A"},"S5","2026-10-23")[1]
    assert assign(rows,{"Tech A"},"S5","2026-10-23",confirmed_s5_techs={"Tech A"})==(rows,[])


def test_same_wo_multi_tech_is_not_split():
    rows=[row(tech="Tech A",closed="2026-10-23T19:00:00"),
          row(tech="Tech B",closed="2026-10-23T19:00:00")]
    assert assign(rows,{"Tech A","Tech B"},"S5","2026-10-23")==(rows,[])
    conflict=[rows[0],row(tech="Tech B",closed="2026-10-23T17:40:00")]
    accepted,pending=assign(conflict,{"Tech A","Tech B"},"S5","2026-10-23")
    assert not accepted and len(pending)==1
    assert assign(conflict,{"Tech A","Tech B"},"S4","2026-10-23",{"101":"S4"})==(conflict,[])


def test_nonsplit_unfiltered():
    rows=[row()]
    assert assign(rows,set(),"S4","2026-10-23")==(rows,[])
    assert assign(rows,{"Tech A"},"S2","2026-10-23")==(rows,[])


def test_in_process_preserved_for_oos():
    rows=[row(status="In Process")]
    assert assign(rows,{"Tech A"},"S5","2026-10-23")==(rows,[])

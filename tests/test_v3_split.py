"""S4/S5 split worker timestamp and MST override tests."""
from v3_split import assign, owner_for_close

def test_split_timestamp_boundaries():
    day="2026-10-23"
    assert owner_for_close("2026-10-23T15:00:00",day)=="S4"
    assert owner_for_close("2026-10-23T20:00:00",day)=="S5"
    assert owner_for_close("2026-10-24T02:00:00",day)=="S5"
    assert owner_for_close("2026-10-23T17:45:00",day) is None
    assert owner_for_close("",day) is None

def test_split_assignment_review_and_override():
    rows=[{"wo_number":"101","tech_name":"Tech A","status":"Closed","closed_at":"2026-10-23T17:45:00","asset_id":"M1"}]
    selected={"Tech A"}
    accepted,pending=assign(rows,selected,"S4","2026-10-23")
    assert accepted==[] and pending[0]["wo_number"]=="101"
    accepted,pending=assign(rows,selected,"S4","2026-10-23",{"101":"S4"})
    assert accepted==rows and not pending
    accepted,pending=assign(rows,selected,"S5","2026-10-23",{"101":"S4"})
    assert not accepted and not pending

def test_nonsplit_never_time_filtered():
    rows=[{"wo_number":"101","tech_name":"Tech A","status":"Closed","closed_at":"","asset_id":"M1"}]
    assert assign(rows,set(),"S4","2026-10-23")== (rows,[])
    assert assign(rows,{"Tech A"},"S2","2026-10-23")== (rows,[])

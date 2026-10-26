"""Synthetic regression tests for final V3 import and Breakdown policy."""
from datetime import datetime
from io import BytesIO
from openpyxl import Workbook
import db
import v3_breakdown
from v3_cetaris import parse_excel, select_records

def sample(wo="100",status="Closed",rfr="Proactive Repair",tech="Tech A",remarks="Adjusted"):
    return {"wo_number":wo,"asset_id":"VOIDFILL0501","status":status,"rfr":rfr,
            "reported_issue":"Side guide not gripping boxes","tech_name":tech,
            "remarks":remarks,"start_at":"2020-01-01T12:00:00","closed_at":"2026-10-22T20:00:00",
            "cause":"WRONG COLUMN"}

def test_normal_shift_does_not_filter_by_labor_start():
    rows=[sample(),sample(tech="Tech B",remarks="Validated; RTS"),sample(tech="Tech C",remarks="Excluded")]
    selected,stats=select_records(rows,{"Tech A","Tech B"},datetime(2026,10,22,12),datetime(2026,10,22,22))
    assert len(selected)==2
    assert stats["unique_happening_wos"]==1
    assert {r["remarks"] for r in selected}=={"Adjusted","Validated; RTS"}

def test_completed_breakdown_requires_mst_closure_review_and_in_process_stays_oos():
    rows=[sample(wo="101",rfr="Breakdown",status="Completed"),
          sample(wo="102",rfr="Breakdown",status="In Process")]
    records,stats=select_records(rows,{"Tech A"},datetime(2026,10,22,12),datetime(2026,10,22,22))
    assert {r["wo_number"]:r["kind"] for r in records}=={"102":"oos"}
    accepted,counts=select_records(rows,{"Tech A"},datetime(2026,10,22,12),
                                   datetime(2026,10,22,22),closure_reviews={"101":"add"})
    assert {r["wo_number"]:r["kind"] for r in accepted}=={"101":"happening","102":"oos"}
    assert "Please close this WO" in next(r["remarks"] for r in accepted if r["wo_number"]=="101")
    assert counts["unique_pm_wos"]==0

def test_only_preventive_maintenance_counts():
    rows=[sample(wo="201",rfr="Preventive Maintenance"),sample(wo="202",rfr="Found During PM"),sample(wo="203",rfr="PM")]
    records,stats=select_records(rows,{"Tech A"},datetime(2026,10,22,12),datetime(2026,10,22,22))
    assert stats["unique_pm_wos"]==1
    assert stats["unique_happening_wos"]==2

def test_parser_reads_actual_wo_close_column():
    wb=Workbook();sh=wb.active
    sh.append(["WO Number","Asset #","WO Status","Primary RFR","Service/Repair Description",
               "Technician","Technician Remarks","WO Closed Date/Time"])
    sh.append(["101","VOIDFILL0501","Closed","Breakdown","Guide stuck","Tech A","Repaired",datetime(2026,10,22,20,0)])
    buf=BytesIO();wb.save(buf)
    row=parse_excel(buf.getvalue())[0]
    assert row["closed_at"]=="2026-10-22T20:00:00"
    assert row["reported_issue"]=="Guide stuck"

def test_parser_in_service_fallback_keeps_close_missing():
    wb=Workbook(); sh=wb.active
    sh.append(["WO Number","Asset #","WO Status","Primary RFR",
               "Service/Repair Description","Technician","Technician Remarks",
               "In Service Date / Time"])
    sh.append(["101","VOIDFILL0501","Closed","Proactive Repair",
               "Guide stuck","Tech A","Repaired",datetime(2026,10,23,22,10)])
    buf=BytesIO(); wb.save(buf)
    row=parse_excel(buf.getvalue())[0]
    assert row["closed_at"]==""
    assert row["in_service_at"]=="2026-10-23T22:10:00"


def test_breakdown_state_tracks_same_wo_and_requires_confirmation(tmp_path):
    db.set_db_path(tmp_path/"test.db");db.init_db()
    selected={"Tech A"}
    v3_breakdown.sync(db,[sample(wo="B1",status="In Process",rfr="Breakdown")],selected,1)
    assert "VOIDFILL0501" in db.get_oos_units()
    v3_breakdown.sync(db,[sample(wo="B1",status="Completed",rfr="Breakdown")],selected,1)
    assert len(v3_breakdown.pending(db))==1
    assert "VOIDFILL0501" in db.get_oos_units()
    v3_breakdown.decide(db,1,{"B1":"rts"})
    assert not v3_breakdown.pending(db)
    assert "VOIDFILL0501" not in db.get_oos_units()
    assert db.get_rts_events(1)==["VOIDFILL0501"]
    v3_breakdown.decide(db,1,{"B1":"rts"})
    assert db.get_rts_events(1)==["VOIDFILL0501"]

def test_other_breakdown_prevents_rts(tmp_path):
    db.set_db_path(tmp_path/"two.db");db.init_db()
    v3_breakdown.sync(db,[sample(wo="B1",status="In Process",rfr="Breakdown"),
                          sample(wo="B2",status="In Process",rfr="Breakdown")],{"Tech A"},2)
    v3_breakdown.sync(db,[sample(wo="B1",status="Closed",rfr="Breakdown")],{"Tech A"},2)
    assert "VOIDFILL0501" in db.get_oos_units()
    v3_breakdown.sync(db,[sample(wo="B2",status="Closed",rfr="Breakdown")],{"Tech A"},2)
    assert "VOIDFILL0501" not in db.get_oos_units()
    assert db.get_rts_events(2)==["VOIDFILL0501"]

def test_manual_hold_blocks_closed_breakdown_rts(tmp_path):
    db.set_db_path(tmp_path/"hold.db");db.init_db()
    v3_breakdown.hold(db,"VOIDFILL0501",True)
    v3_breakdown.sync(db,[sample(wo="B1",status="In Process",rfr="Breakdown")],{"Tech A"},3)
    v3_breakdown.sync(db,[sample(wo="B1",status="Closed",rfr="Breakdown")],{"Tech A"},3)
    assert "VOIDFILL0501" in db.get_oos_units()
    assert db.get_rts_events(3)==[]


def test_breakdown_tracked_wo_closes_on_later_shift_even_with_unselected_tech(tmp_path):
    db.set_db_path(tmp_path/"tracked_later.db");db.init_db()
    v3_breakdown.sync(db,[sample(wo="CROSS",status="In Process",rfr="Breakdown",
                                  tech="Tech S3")],{"Tech S3"},1,
                      allowed_new_wos={"CROSS"})
    assert "VOIDFILL0501" in db.get_oos_units()
    # New shift selects a different technician. The old tracked WO can
    # transition to Closed, but a brand-new unrelated WO must not be added.
    v3_breakdown.sync(db,[
        sample(wo="CROSS",status="Closed",rfr="Breakdown",tech="Tech S3"),
        sample(wo="OTHER",status="In Process",rfr="Breakdown",tech="Tech S4")
    ],{"Tech S4"},2,allowed_new_wos=set())
    assert "VOIDFILL0501" not in db.get_oos_units()
    assert db.get_rts_events(2)==["VOIDFILL0501"]
    with db._conn() as c:
        assert c.execute("SELECT 1 FROM v3_breakdown_state WHERE wo_number='OTHER'").fetchone() is None


def test_first_seen_closed_breakdown_appears_in_rts_without_prior_oos(tmp_path):
    """Newly imported, verified CLOSED Breakdown must count as RTS even if
    its earlier In Process file was never exported for this shift."""
    db.set_db_path(tmp_path/"first_closed_breakdown.db");db.init_db()
    rows=[sample(wo="BD-NEW-CLOSED",status="Closed",rfr="Breakdown")]
    v3_breakdown.sync(db,rows,{"Tech A"},11,allowed_new_wos={"BD-NEW-CLOSED"})
    assert db.get_oos_units()==[]
    assert db.get_rts_events(11)==["VOIDFILL0501"]
    # Re-import cannot duplicate the event.
    v3_breakdown.sync(db,rows,{"Tech A"},11,allowed_new_wos={"BD-NEW-CLOSED"})
    assert db.get_rts_events(11)==["VOIDFILL0501"]


def test_first_seen_closed_breakdown_never_bypasses_manual_oos_hold(tmp_path):
    db.set_db_path(tmp_path/"closed_hold.db");db.init_db()
    db.add_oos_unit("VOIDFILL0501")
    v3_breakdown.hold(db,"VOIDFILL0501",True)
    v3_breakdown.sync(db,[sample(wo="BD-HELD",status="Closed",rfr="Breakdown")],
                      {"Tech A"},12,allowed_new_wos={"BD-HELD"})
    assert db.get_oos_units()==["VOIDFILL0501"]
    assert db.get_rts_events(12)==[]


def test_in_process_to_closed_rts_is_not_reversed_by_stale_export(tmp_path):
    db.set_db_path(tmp_path/"lifecycle.db");db.init_db()
    start=[sample(wo="BD-LIVE",status="In Process",rfr="Breakdown")]
    closed=[sample(wo="BD-LIVE",status="Closed",rfr="Breakdown")]
    v3_breakdown.sync(db,start,{"Tech A"},20,allowed_new_wos={"BD-LIVE"})
    assert db.get_oos_units()==["VOIDFILL0501"]
    assert db.get_rts_events(20)==[]
    v3_breakdown.sync(db,closed,{"Tech A"},20,allowed_new_wos={"BD-LIVE"})
    assert db.get_oos_units()==[]
    assert db.get_rts_events(20)==["VOIDFILL0501"]
    v3_breakdown.sync(db,start,{"Tech A"},20,allowed_new_wos={"BD-LIVE"})
    assert db.get_oos_units()==[]
    assert db.get_rts_events(20)==["VOIDFILL0501"]


def test_several_closed_breakdown_wos_same_asset_do_not_double_rts(tmp_path):
    db.set_db_path(tmp_path/"several_closed.db");db.init_db()
    records=[sample(wo="BD-1",status="Closed",rfr="Breakdown"),
             sample(wo="BD-2",status="Closed",rfr="Breakdown")]
    v3_breakdown.sync(db,records,{"Tech A"},35,allowed_new_wos={"BD-1","BD-2"})
    assert db.get_rts_events(35)==["VOIDFILL0501"]


def test_repeat_import_heals_legacy_closed_breakdown_processed_without_rts(tmp_path):
    """Old V3 set rts_logged=1 even with no rts_log row.
    Reimport must repair it in the *same* shift, without double counting."""
    db.set_db_path(tmp_path/"old_closed_missing_rts.db");db.init_db()
    closed=[sample(wo="BD-LEGACY",status="Closed",rfr="Breakdown")]
    v3_breakdown.sync(db,closed,{"Tech A"},23,allowed_new_wos={"BD-LEGACY"})
    assert db.get_rts_events(23)==["VOIDFILL0501"]
    # Simulate old database corruption: Closed and marked processed, but
    # the RTS event was never created by the historical implementation.
    with db._conn() as c:
        c.execute("DELETE FROM rts_log WHERE shift_id=23")
        state=c.execute("SELECT status,rts_logged FROM v3_breakdown_state WHERE wo_number=?",
                        ("BD-LEGACY",)).fetchone()
        assert state["status"]=="closed" and state["rts_logged"]==1
    assert db.get_rts_events(23)==[]
    v3_breakdown.sync(db,closed,{"Tech A"},23,allowed_new_wos={"BD-LEGACY"})
    assert db.get_rts_events(23)==["VOIDFILL0501"]
    v3_breakdown.sync(db,closed,{"Tech A"},23,allowed_new_wos={"BD-LEGACY"})
    assert db.get_rts_events(23)==["VOIDFILL0501"]
    # Repeated old WOs must not be re-credited on another shift.
    v3_breakdown.sync(db,closed,{"Tech A"},24,allowed_new_wos=set())
    assert db.get_rts_events(24)==[]


def test_mst_removed_rts_does_not_reappear_on_repeat_import(tmp_path):
    db.set_db_path(tmp_path/"manual_rts_removal.db");db.init_db()
    closed=[sample(wo="MANUAL-REMOVE",status="Closed",rfr="Breakdown")]
    v3_breakdown.sync(db,closed,{"Tech A"},5,allowed_new_wos={"MANUAL-REMOVE"})
    assert db.get_rts_events(5)==["VOIDFILL0501"]
    db.delete_rts_event(5,"VOIDFILL0501")
    v3_breakdown.suppress_rts_reimport(db,5,"VOIDFILL0501")
    v3_breakdown.sync(db,closed,{"Tech A"},5,allowed_new_wos={"MANUAL-REMOVE"})
    assert db.get_rts_events(5)==[]


def test_workshop_manager_review_required_routes_to_completed_pending_rts(tmp_path):
    """Restore real-workflow status alias from Code Puppy workshop branch."""
    db.set_db_path(tmp_path/"review_breakdown.db");db.init_db()
    v3_breakdown.sync(db,[sample(wo="REVIEW",status="In Process",rfr="Breakdown")],
                      {"Tech A"},1,allowed_new_wos={"REVIEW"})
    v3_breakdown.sync(db,[sample(wo="REVIEW",status="Manager Review Required",rfr="Breakdown")],
                      {"Tech A"},1,allowed_new_wos={"REVIEW"})
    assert db.get_oos_units()==["VOIDFILL0501"]
    assert [p["wo_number"] for p in v3_breakdown.pending(db)]==["REVIEW"]
    v3_breakdown.decide(db,1,{"REVIEW":"rts"})
    assert db.get_oos_units()==[]
    assert db.get_rts_events(1)==["VOIDFILL0501"]

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

def test_completed_breakdown_happening_and_in_process_oos():
    rows=[sample(wo="101",rfr="Breakdown",status="Completed"),
          sample(wo="102",rfr="Breakdown",status="In Process")]
    records,stats=select_records(rows,{"Tech A"},datetime(2026,10,22,12),datetime(2026,10,22,22))
    assert {r["wo_number"]:r["kind"] for r in records}=={"101":"happening","102":"oos"}

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

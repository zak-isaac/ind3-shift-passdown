"""Direct unit tests for v3_breakdown.py's status handling.

Regression coverage for: real Cetaris exports at this facility don't use a
literal "Completed" status for breakdown WOs -- a tech finishing the repair
puts it at "Manager Review Required" (pending admin sign-off/closure).
sync() only recognized the literal string "completed", so any breakdown WO
sitting at "Manager Review Required" was silently skipped entirely: no OOS
entry, no pending RTS decision, nothing -- discovered 2026-10-23 when a real
uploaded file's breakdown WO never showed up anywhere.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import db
import v3_breakdown


def _row(wo, tech, asset, status, rfr="Breakdown"):
    return {"wo_number": wo, "tech_name": tech, "asset_id": asset, "status": status, "rfr": rfr}


def test_manager_review_required_is_treated_as_completed(tmp_path):
    db.set_db_path(tmp_path / "breakdown.db")
    db.init_db()
    rows = [_row("500", "Tech A", "M1", "Manager Review Required")]
    updated = v3_breakdown.sync(db, rows, {"Tech A"}, shift_id=1)
    assert updated == 1
    pending = v3_breakdown.pending(db)
    assert len(pending) == 1
    assert pending[0]["wo_number"] == "500"
    assert pending[0]["asset_id"] == "M1"


def test_literal_completed_still_works(tmp_path):
    """The alias is additive -- a real literal 'Completed' status (if a
    different export ever uses that wording) must keep working too."""
    db.set_db_path(tmp_path / "breakdown2.db")
    db.init_db()
    rows = [_row("501", "Tech A", "M2", "Completed")]
    updated = v3_breakdown.sync(db, rows, {"Tech A"}, shift_id=1)
    assert updated == 1
    assert [p["wo_number"] for p in v3_breakdown.pending(db)] == ["501"]


def test_in_process_still_marks_oos(tmp_path):
    db.set_db_path(tmp_path / "breakdown3.db")
    db.init_db()
    rows = [_row("502", "Tech A", "M3", "In process")]
    v3_breakdown.sync(db, rows, {"Tech A"}, shift_id=1)
    assert "M3" in db.get_oos_units()


def test_unrecognized_status_is_ignored_not_crashed(tmp_path):
    db.set_db_path(tmp_path / "breakdown4.db")
    db.init_db()
    rows = [_row("503", "Tech A", "M4", "Some Future Cetaris Wording")]
    updated = v3_breakdown.sync(db, rows, {"Tech A"}, shift_id=1)
    assert updated == 0
    assert v3_breakdown.pending(db) == []


def test_wo_closed_on_first_sighting_still_logs_rts(tmp_path):
    """A breakdown WO whose entire lifecycle (In Process -> Closed) happens
    within a single Cetaris export shows up Closed the FIRST time this
    tracker ever sees it -- it was never separately added to oos_units, but
    it's still a real 'equipment is back up' event the next shift needs to
    see. Regression coverage for: CR0009/DD0247 closed breakdowns that never
    appeared in Returned to Service, 2026-10-23."""
    db.set_db_path(tmp_path / "breakdown5.db")
    db.init_db()
    rows = [_row("28025965", "Tech A", "CR0009", "Closed")]
    v3_breakdown.sync(db, rows, {"Tech A"}, shift_id=42)
    events = db.get_rts_events(42)
    assert "CR0009" in events


def test_rts_is_not_double_logged_on_reimport(tmp_path):
    db.set_db_path(tmp_path / "breakdown6.db")
    db.init_db()
    rows = [_row("600", "Tech A", "M5", "Closed")]
    v3_breakdown.sync(db, rows, {"Tech A"}, shift_id=1)
    v3_breakdown.sync(db, rows, {"Tech A"}, shift_id=1)
    with db._conn() as c:
        count = c.execute("SELECT COUNT(*) FROM rts_log WHERE unit=?", ("M5",)).fetchone()[0]
    assert count == 1

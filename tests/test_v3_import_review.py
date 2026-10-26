"""Tests for v3_import_review.py: the actionable Needs Review queue.

Ported from the personal-GitHub production branch's design (commit
ba2cf8a), adapted to Workshop's engine shape. Deliberately scoped narrower
than the original -- no standing shift-ownership override (see module
docstring for why that's intentionally excluded).
"""
import json
import db
import v3_import_review as review
from v3_import_audit import build_audit


def _ignored_item(wo="100", reason="Outside this shift or missing verified technician completion evidence",
                   includable=True, raw_row=None):
    return {
        "wo_number": wo, "asset_id": "A1", "status": "Closed",
        "technicians": "Tech A", "start_at": "2026-10-20T14:00:00",
        "reason": reason, "includable": includable,
        "raw_row": raw_row or {
            "wo_number": wo, "asset_id": "A1", "tech_name": "Tech A",
            "reported_issue": "Reported", "cause": "", "remarks": "Done", "rfr": "Breakdown",
        },
    }


def test_only_includable_items_are_persisted(tmp_path):
    db.set_db_path(tmp_path / "review.db"); db.init_db()
    review.record(db, 1, [_ignored_item("100", includable=True), _ignored_item("200", includable=False)])
    pending = review.pending(db, 1)
    assert {p["wo_number"] for p in pending} == {"100"}


def _ensure_entry_meta(tmp_path_db):
    """v3_entry_meta is normally created lazily by v3_routes.py's router-level
    ensure_tables() on first request. These unit tests call v3_import_review
    directly, bypassing the router, so create the same schema here."""
    with db._conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS v3_entry_meta (
          entry_id INTEGER PRIMARY KEY, shift_id INTEGER NOT NULL,
          reported_issue TEXT NOT NULL DEFAULT '', cause_text TEXT NOT NULL DEFAULT '',
          original_remarks TEXT NOT NULL DEFAULT '[]', mst_edited INTEGER NOT NULL DEFAULT 0,
          needs_review INTEGER NOT NULL DEFAULT 0, pending_json TEXT,
          imported_v3 INTEGER NOT NULL DEFAULT 0, source_snapshot TEXT NOT NULL DEFAULT '',
          rfr_type TEXT NOT NULL DEFAULT ''
        );
        """)


def test_include_inserts_a_real_entry_and_resolves(tmp_path):
    db.set_db_path(tmp_path / "include.db"); db.init_db()
    _ensure_entry_meta(tmp_path)
    sid = db.start_shift("2026-10-20", "S2", "Test MST")
    review.record(db, sid, [_ignored_item("100")])
    review_id = review.pending(db, sid)[0]["id"]
    assert review.include(db, sid, review_id)
    assert {e["wo_number"] for e in db.get_entries(sid)} == {"100"}
    assert review.pending(db, sid) == []


def test_include_routes_pm_coded_wo_to_pm_wos_not_entries(tmp_path):
    db.set_db_path(tmp_path / "include_pm.db"); db.init_db()
    sid = db.start_shift("2026-10-20", "S2", "Test MST")
    raw = {"wo_number": "500", "asset_id": "A1", "tech_name": "Tech A",
           "reported_issue": "PM", "remarks": "Done", "rfr": "Preventive Maintenance", "pm_code": "E.001.BALER.WALK"}
    review.record(db, sid, [_ignored_item("500", raw_row=raw)])
    review_id = review.pending(db, sid)[0]["id"]
    assert review.include(db, sid, review_id)
    assert db.get_pm_count(sid) == 1
    assert db.get_entries(sid) == []


def test_dismiss_resolves_without_inserting_anything(tmp_path):
    db.set_db_path(tmp_path / "dismiss.db"); db.init_db()
    sid = db.start_shift("2026-10-20", "S2", "Test MST")
    review.record(db, sid, [_ignored_item("100")])
    review_id = review.pending(db, sid)[0]["id"]
    assert review.dismiss(db, sid, review_id)
    assert db.get_entries(sid) == []
    assert review.pending(db, sid) == []


def test_resolved_item_is_not_recreated_on_reimport(tmp_path):
    """The MST's decision on a specific WO must not be re-asked every time
    the same Cetaris file is re-uploaded."""
    db.set_db_path(tmp_path / "noreask.db"); db.init_db()
    sid = db.start_shift("2026-10-20", "S2", "Test MST")
    review.record(db, sid, [_ignored_item("100")])
    review_id = review.pending(db, sid)[0]["id"]
    review.dismiss(db, sid, review_id)
    review.record(db, sid, [_ignored_item("100")])  # same file re-imported
    assert review.pending(db, sid) == []


def test_include_returns_false_for_unknown_or_already_resolved_id(tmp_path):
    db.set_db_path(tmp_path / "unknown.db"); db.init_db()
    sid = db.start_shift("2026-10-20", "S2", "Test MST")
    assert review.include(db, sid, 9999) is False
    assert review.dismiss(db, sid, 9999) is False


def test_build_audit_marks_clear_cut_exclusions_not_includable():
    """Only genuinely evidence-ambiguous reasons are includable -- a WO
    already credited to another shift, from an unselected technician, or
    not yet Closed/Completed must never be offered for manual override."""
    from datetime import date
    rows = [
        {"wo_number": "1", "tech_name": "Tech A", "status": "Closed",
         "asset_id": "A1", "start_at": "2026-10-20T14:00:00"},
    ]
    audit = build_audit(rows, {"Tech B"}, [], [], shift_type="S2", shift_day="2026-10-20")
    item = audit["ignored"][0]
    assert item["reason"] == "Technician not selected (other shift or vendor)"
    assert item["includable"] is False

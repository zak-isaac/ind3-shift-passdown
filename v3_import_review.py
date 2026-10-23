"""Review queue for WOs skipped during Cetaris import (window/ambiguity).

select_records() in v3_cetaris.py already computes exactly which WOs got
excluded or flagged ambiguous, with the real reason -- but until now that
detail was thrown away the moment confirm_import() turned it into an
aggregate count for the notice banner ("Skipped 12 outside this shift's
time window..."). An MST had no way to see WHICH WOs, their close times, or
to manually pull one back in. This hit overnight shifts (S3, S5) hardest,
since midnight-crossing timestamps are the most likely to disagree with a
shift's computed window. Root-caused and fixed 2026-10-23.

This module persists that detail per shift and lets an MST explicitly
Include (insert as a normal entry, same as an in-window row would have
been) or Dismiss (acknowledge and drop) each flagged WO.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone


def ensure_tables(db):
    with db._conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS v3_import_review (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          shift_id INTEGER NOT NULL, wo_number TEXT NOT NULL, tech_name TEXT NOT NULL,
          asset_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
          start_at TEXT NOT NULL DEFAULT '', closed_at TEXT NOT NULL DEFAULT '',
          kind TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '',
          row_json TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL
        );
        """)


def record(db, shift_id, items_by_kind: dict[str, list[dict]]) -> None:
    """items_by_kind: {"ambiguous": [raw rows...], "excluded": [raw rows...]}.

    Each row already carries `reason` (see v3_cetaris.select_records()).
    A WO+tech already explicitly resolved (Included or Dismissed) by the MST
    stays resolved even if a later re-import recomputes the same flag --
    their decision isn't re-litigated every re-upload of the same file.
    """
    ensure_tables(db)
    now = datetime.now(timezone.utc).isoformat()
    with db._conn() as c:
        for kind, items in items_by_kind.items():
            for r in items:
                wo = str(r.get("wo_number", "")).strip()
                tech = str(r.get("tech_name", "")).strip()
                if not wo or not tech:
                    continue
                already_resolved = c.execute(
                    "SELECT 1 FROM v3_import_review WHERE shift_id=? AND wo_number=? AND tech_name=? AND resolved=1",
                    (shift_id, wo, tech),
                ).fetchone()
                if already_resolved:
                    continue
                c.execute(
                    "DELETE FROM v3_import_review WHERE shift_id=? AND wo_number=? AND tech_name=? AND resolved=0",
                    (shift_id, wo, tech),
                )
                c.execute(
                    """INSERT INTO v3_import_review
                       (shift_id,wo_number,tech_name,asset_id,status,start_at,closed_at,
                        kind,reason,row_json,resolved,created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,0,?)""",
                    (shift_id, wo, tech, r.get("asset_id", ""), r.get("status", ""),
                     r.get("start_at", ""), r.get("closed_at", ""), kind, r.get("reason", ""),
                     json.dumps(r), now),
                )


def pending(db, shift_id) -> list[dict]:
    ensure_tables(db)
    with db._conn() as c:
        rows = c.execute(
            "SELECT * FROM v3_import_review WHERE shift_id=? AND resolved=0 ORDER BY created_at",
            (shift_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def include(db, shift_id, review_id) -> bool:
    """Manually include a flagged WO into this shift's entries, exactly as
    an in-window row would have been handled during the normal import.
    No-ops (but still marks resolved) if an entry for this WO+tech already
    exists -- the MST already has it, nothing to add."""
    ensure_tables(db)
    with db._conn() as c:
        row = c.execute(
            "SELECT * FROM v3_import_review WHERE id=? AND shift_id=? AND resolved=0",
            (review_id, shift_id),
        ).fetchone()
    if not row:
        return False
    r = json.loads(row["row_json"])
    if str(r.get("pm_code", "")).strip():
        db.upsert_pm_wo(shift_id, r["wo_number"], r.get("asset_id", ""), r["tech_name"])
    else:
        existing = db.get_cmms_entry(shift_id, r["wo_number"], r["tech_name"])
        if not existing:
            remarks_list = [r["remarks"]] if r.get("remarks") else []
            eid = db.upsert_cmms_entry(
                shift_id, r["wo_number"], r.get("asset_id", ""),
                r.get("reported_issue", ""), json.dumps(remarks_list), r["tech_name"],
            )
            with db._conn() as c:
                c.execute(
                    """INSERT INTO v3_entry_meta
                       (entry_id,shift_id,reported_issue,cause_text,original_remarks,
                        imported_v3,source_snapshot,rfr_type)
                       VALUES (?,?,?,?,?,1,?,?)""",
                    (eid, shift_id, r.get("reported_issue", ""), r.get("cause", ""),
                     json.dumps(remarks_list), json.dumps(r), r.get("rfr", "")),
                )
    with db._conn() as c:
        c.execute("UPDATE v3_import_review SET resolved=1 WHERE id=?", (review_id,))
    return True


def dismiss(db, shift_id, review_id) -> bool:
    ensure_tables(db)
    with db._conn() as c:
        cur = c.execute(
            "UPDATE v3_import_review SET resolved=1 WHERE id=? AND shift_id=? AND resolved=0",
            (review_id, shift_id),
        )
        return cur.rowcount > 0

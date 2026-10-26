"""Actionable review queue for WOs excluded from a Cetaris import.

Ported from the personal-GitHub production branch's v3_import_review.py
(github.com/zak-isaac/ind3-shift-passdown, commit ba2cf8a) and adapted to
Workshop's actual engine shape, 2026-10-26.

v3_import_audit.build_audit() already computes exactly which WOs got
excluded, with the real reason -- but until now that detail was only ever
shown read-only. An MST had no way to act on it. This hits overnight
shifts (S3, S5) hardest, since midnight-crossing timestamps are the most
likely to disagree with a shift's computed window.

Scope deliberately narrower than the ported original: this module never
lets an MST force a WO's shift OWNERSHIP (the original's "ownership"
review kind / assign_shift()). Workshop's v3_split.assign() already
documents why that's rejected here: "Legacy decisions are intentionally
ignored: an administrative choice cannot override Cetaris technician
completion evidence." Re-introducing a standing manual override would
directly contradict that already-established design decision.

What IS ported: a human, reviewing ONE specific WO with full visibility
into why it was excluded, can manually Include it -- but only for the
genuinely evidence-ambiguous reasons (build_audit() marks these
`includable`), never for a WO that's already credited to another shift,
not from a selected technician, or not yet Closed/Completed. Those remain
hard exclusions, not judgment calls.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone


def ensure_tables(db):
    with db._conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS v3_import_review (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          shift_id INTEGER NOT NULL, wo_number TEXT NOT NULL,
          asset_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT '',
          technicians TEXT NOT NULL DEFAULT '', start_at TEXT NOT NULL DEFAULT '',
          updated_at TEXT NOT NULL DEFAULT '',
          reason TEXT NOT NULL DEFAULT '', row_json TEXT NOT NULL,
          resolved INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
        );
        """)


def record(db, shift_id, ignored_items: list[dict]) -> None:
    """Persist only the genuinely includable ignored WOs from build_audit()'s
    `ignored` list. A WO already explicitly resolved (Included/Dismissed) by
    the MST stays resolved even if a later re-import recomputes the same
    flag -- their decision isn't re-litigated every re-upload of the file.
    """
    ensure_tables(db)
    now = datetime.now(timezone.utc).isoformat()
    with db._conn() as c:
        for item in ignored_items:
            if not item.get("includable"):
                continue
            wo = str(item.get("wo_number", "")).strip()
            if not wo:
                continue
            already_resolved = c.execute(
                "SELECT 1 FROM v3_import_review WHERE shift_id=? AND wo_number=? AND resolved=1",
                (shift_id, wo),
            ).fetchone()
            if already_resolved:
                continue
            c.execute(
                "DELETE FROM v3_import_review WHERE shift_id=? AND wo_number=? AND resolved=0",
                (shift_id, wo),
            )
            c.execute(
                """INSERT INTO v3_import_review
                   (shift_id,wo_number,asset_id,status,technicians,start_at,updated_at,
                    reason,row_json,resolved,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,0,?)""",
                (shift_id, wo, item.get("asset_id", ""), item.get("status", ""),
                 item.get("technicians", ""), item.get("start_at", ""), item.get("updated_at", ""),
                 item.get("reason", ""), json.dumps(item.get("raw_row") or {}), now),
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
    an attributed row would have been handled during the normal import.
    No-ops (but still marks resolved) if an entry for this WO already
    exists -- the MST already has it, nothing to add.
    """
    from v3_cetaris import _is_pm_category

    ensure_tables(db)
    with db._conn() as c:
        row = c.execute(
            "SELECT * FROM v3_import_review WHERE id=? AND shift_id=? AND resolved=0",
            (review_id, shift_id),
        ).fetchone()
    if not row:
        return False
    r = json.loads(row["row_json"])
    if not r or not r.get("tech_name"):
        return False
    if _is_pm_category(r):
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
                    """INSERT OR IGNORE INTO v3_entry_meta
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

"""Review queue for WOs skipped or ambiguously owned during Cetaris import.

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

2026-10-24: v3_split.py's S1-S5 technician-evidence attribution engine adds
a second review kind, "ownership" -- a WO whose owning SHIFT (not just
whether to include it) is ambiguous, spanning possibly multiple
technicians and labor rows. Resolving one of these records a standing
ownership decision (v3_wo_ownership_decisions) that any future import of
that WO number honors automatically, regardless of which shift re-uploads
it next -- the MST's call isn't re-asked every re-export.
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
        CREATE TABLE IF NOT EXISTS v3_wo_ownership_decisions (
          wo_number TEXT PRIMARY KEY, shift_type TEXT NOT NULL, decided_at TEXT NOT NULL
        );
        """)


def ownership_decisions(db) -> dict[str, str]:
    """{wo_number: shift_type} for every standing MST ownership call. Fed into
    v3_split.assign()'s `decisions` on every import, by any shift, so a
    choice made once is honored on the next re-upload without re-asking."""
    ensure_tables(db)
    with db._conn() as c:
        rows = c.execute("SELECT wo_number, shift_type FROM v3_wo_ownership_decisions").fetchall()
    return {r["wo_number"]: r["shift_type"] for r in rows}


def record(db, shift_id, items_by_kind: dict[str, list[dict]]) -> None:
    """items_by_kind: {"ambiguous": [...], "excluded": [...], "ownership": [...]}.

    "ambiguous"/"excluded" rows carry `tech_name`+`reason` (see
    v3_cetaris.select_records()). "ownership" items come from
    v3_split.assign()'s `pending` list and are WO-level (possibly several
    technicians), so they're keyed by wo_number alone, with tech_name left
    blank and the technician list folded into row_json for display.

    A WO+tech already explicitly resolved (Included/Dismissed/Assigned) by
    the MST stays resolved even if a later re-import recomputes the same
    flag -- their decision isn't re-litigated every re-upload of the same file.
    """
    ensure_tables(db)
    now = datetime.now(timezone.utc).isoformat()
    with db._conn() as c:
        for kind, items in items_by_kind.items():
            for r in items:
                wo = str(r.get("wo_number", "")).strip()
                tech = "" if kind == "ownership" else str(r.get("tech_name", "")).strip()
                if not wo or (kind != "ownership" and not tech):
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
    items = [dict(r) for r in rows]
    for item in items:
        if item["kind"] == "ownership":
            # row_json carries the WO-level fields (possibly multiple
            # technicians) that don't fit the single-tech_name column shape.
            extra = json.loads(item["row_json"])
            item["technicians"] = extra.get("technicians", "")
            item["time_source"] = extra.get("time_source", "")
    return items


def include(db, shift_id, review_id) -> bool:
    """Manually include a flagged WO into this shift's entries, exactly as
    an in-window row would have been handled during the normal import.
    No-ops (but still marks resolved) if an entry for this WO+tech already
    exists -- the MST already has it, nothing to add.

    "ownership" kind items (v3_split.py's pending WO-level, multi-technician
    reviews) don't carry a single tech_name/reported_issue/etc. and must be
    resolved via assign_shift() instead -- rejected here rather than crashing
    on a missing key.
    """
    ensure_tables(db)
    with db._conn() as c:
        row = c.execute(
            "SELECT * FROM v3_import_review WHERE id=? AND shift_id=? AND resolved=0 AND kind!='ownership'",
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


def assign_shift(db, shift_id, review_id, shift_type) -> bool:
    """Resolve an "ownership" review item by choosing its owning shift.

    Unlike include()/dismiss(), this never inserts an entry directly -- the
    chosen shift may not be the one reviewing it (e.g. an S4 MST resolving a
    WO that actually belongs to S3). Instead it records a standing decision
    (v3_wo_ownership_decisions) that v3_split.assign() honors on the next
    import of that WO, by whichever shift re-uploads it.
    """
    ensure_tables(db)
    if shift_type not in ("S1", "S2", "S3", "S4", "S5"):
        return False
    with db._conn() as c:
        row = c.execute(
            "SELECT * FROM v3_import_review WHERE id=? AND shift_id=? AND kind='ownership' AND resolved=0",
            (review_id, shift_id),
        ).fetchone()
        if not row:
            return False
        now = datetime.now(timezone.utc).isoformat()
        c.execute(
            """INSERT INTO v3_wo_ownership_decisions(wo_number,shift_type,decided_at) VALUES (?,?,?)
               ON CONFLICT(wo_number) DO UPDATE SET shift_type=excluded.shift_type, decided_at=excluded.decided_at""",
            (row["wo_number"], shift_type, now),
        )
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

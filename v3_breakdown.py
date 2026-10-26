"""Breakdown-only WO lifecycle. Sidecar state; never infer RTS from non-breakdown WOs."""
from __future__ import annotations
from datetime import datetime, timezone

def ensure_tables(db):
    with db._conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS v3_breakdown_state (
            wo_number TEXT PRIMARY KEY, asset_id TEXT NOT NULL,
            status TEXT NOT NULL, updated_shift_id INTEGER NOT NULL,
            rts_decision TEXT NOT NULL DEFAULT '',
            rts_logged INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS v3_oos_overrides (
            asset_id TEXT PRIMARY KEY, keep_oos INTEGER NOT NULL DEFAULT 1
        );
        """)

def _unresolved(c, asset, exclude_wo=""):
    return c.execute("""SELECT 1 FROM v3_breakdown_state
        WHERE asset_id=? AND wo_number<>?
          AND (status='in process' OR (status='completed' AND rts_decision<>'rts'))
        LIMIT 1""", (asset,exclude_wo)).fetchone() is not None

def _manual_hold(c, asset):
    row=c.execute("SELECT keep_oos FROM v3_oos_overrides WHERE asset_id=?", (asset,)).fetchone()
    return bool(row and row[0])

def _maybe_rts(db, c, wo, asset, shift_id):
    if _unresolved(c,asset,wo) or _manual_hold(c,asset):
        return False
    state=c.execute("SELECT rts_logged FROM v3_breakdown_state WHERE wo_number=?", (wo,)).fetchone()
    if not state or state[0]: return False
    # A first-seen CLOSED Breakdown also represents a completed repair.
    # The earlier In Process export may not be available in this shift's
    # Cetaris file. Such a WO is allowed here only after actual technician
    # completion/shift attribution (or if it was already tracked as OOS).
    # Do not invent RTS when any other Breakdown remains unresolved or the
    # MST explicitly holds the asset Out of Service (checked above).
    present=c.execute("SELECT 1 FROM oos_units WHERE unit=?", (asset,)).fetchone()
    if present:
        c.execute("DELETE FROM oos_units WHERE unit=?", (asset,))
    # Multiple Closed Breakdown WOs for the same unit in one shift should
    # produce one RTS display entry, not inflated duplicate unit counts.
    already=c.execute("SELECT 1 FROM rts_log WHERE shift_id=? AND unit=? LIMIT 1",
                      (shift_id,asset)).fetchone()
    if not already:
        c.execute("INSERT INTO rts_log(shift_id,unit,removed_at) VALUES (?,?,?)",
                  (shift_id,asset,datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
    c.execute("UPDATE v3_breakdown_state SET rts_logged=1 WHERE wo_number=?", (wo,))
    return bool(present) or not bool(already)

def sync(db, rows, selected, shift_id, allowed_new_wos=None):
    """Apply tracked-WO status updates from later exports without leaking new WOs from other shifts."""
    ensure_tables(db)
    priority={"in process":1,"completed":2,"closed":3}
    allowed_new_wos = None if allowed_new_wos is None else set(allowed_new_wos)
    selected_wos={}
    with db._conn() as c:
        tracked={r[0] for r in c.execute("SELECT wo_number FROM v3_breakdown_state")}
    for r in rows:
        if r.get("rfr","").strip().casefold()!="breakdown": continue
        wo=str(r.get("wo_number","")).strip()
        status=str(r.get("status","")).strip().casefold()
        asset=str(r.get("asset_id","")).strip()
        if not wo or not asset or status not in priority: continue
        if wo not in tracked and (r.get("tech_name") not in selected or
                                  (allowed_new_wos is not None and wo not in allowed_new_wos)):
            continue
        previous=selected_wos.get(wo)
        if previous is None or priority[status]>priority[previous[0]]:
            selected_wos[wo]=(status,asset)
    updated=0
    with db._conn() as c:
        for wo,(status,asset) in selected_wos.items():
            old=c.execute("SELECT * FROM v3_breakdown_state WHERE wo_number=?", (wo,)).fetchone()
            # Do not reopen completed/closed repairs because a later XLSX
            # contains an older labor line for the same WO number.
            if old and priority[status] < priority.get(old["status"],0):
                continue
            if old and old["status"]==status and old["asset_id"]==asset:
                continue
            decision = (old["rts_decision"] if old and old["status"]==status else "")
            logged = (old["rts_logged"] if old and old["status"]==status else 0)
            c.execute("""INSERT INTO v3_breakdown_state
                (wo_number,asset_id,status,updated_shift_id,rts_decision,rts_logged,updated_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(wo_number) DO UPDATE SET
                asset_id=excluded.asset_id,status=excluded.status,
                updated_shift_id=excluded.updated_shift_id,
                rts_decision=excluded.rts_decision,rts_logged=excluded.rts_logged,
                updated_at=excluded.updated_at""",
                (wo,asset,status,shift_id,decision,logged,datetime.now(timezone.utc).isoformat()))
            if status=="in process":
                c.execute("INSERT OR IGNORE INTO oos_units(unit,added_at) VALUES (?,?)",
                          (asset,datetime.now(timezone.utc).isoformat()))
            updated+=1
        # Resolve Closed WOs only after ALL statuses have been processed.
        for wo,(status,asset) in selected_wos.items():
            if status=="closed":
                _maybe_rts(db,c,wo,asset,shift_id)
    return updated

def pending(db):
    ensure_tables(db)
    with db._conn() as c:
        return [dict(r) for r in c.execute("""SELECT wo_number,asset_id,updated_shift_id
            FROM v3_breakdown_state WHERE status='completed' AND rts_decision=''
            ORDER BY updated_at""")]

def decide(db, shift_id, decisions):
    """Batch decisions: {wo_number: 'rts'|'keep_oos'}; idempotent."""
    ensure_tables(db)
    with db._conn() as c:
        for wo,decision in decisions.items():
            if decision not in ("rts","keep_oos"): continue
            state=c.execute("SELECT * FROM v3_breakdown_state WHERE wo_number=?", (wo,)).fetchone()
            if not state or state["status"]!="completed" or state["rts_decision"]: continue
            asset=state["asset_id"]
            c.execute("UPDATE v3_breakdown_state SET rts_decision=? WHERE wo_number=?", (decision,wo))
            if decision=="keep_oos":
                c.execute("INSERT OR IGNORE INTO oos_units(unit,added_at) VALUES (?,?)",
                          (asset,datetime.now(timezone.utc).isoformat()))
            else:
                _maybe_rts(db,c,wo,asset,shift_id)

def hold(db, asset, keep=True):
    ensure_tables(db)
    with db._conn() as c:
        if keep:
            c.execute("INSERT INTO v3_oos_overrides(asset_id,keep_oos) VALUES (?,1) ON CONFLICT(asset_id) DO UPDATE SET keep_oos=1",(asset,))
        else:
            c.execute("DELETE FROM v3_oos_overrides WHERE asset_id=?",(asset,))

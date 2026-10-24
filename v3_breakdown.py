"""Breakdown-only WO lifecycle. Sidecar state; never infer RTS from non-breakdown WOs.

Ported from the personal-GitHub operational-integration branch
(github.com/zak-isaac/ind3-shift-passdown, commits 02caf40/bb73c86/c18dd00).
Wired into v3_routes.py's confirm_import() for every shift type.
"""
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
    # A resolving breakdown is a real RTS event worth reporting even if the
    # asset was never separately in oos_units -- e.g. a WO whose entire
    # lifecycle (In Process -> Closed) happened within a single Cetaris
    # export, so the FIRST time this tracker ever saw it, it was already
    # Closed. The next shift still needs to know "this equipment is back
    # up" regardless of our own bookkeeping gaps. Only the oos_units cleanup
    # is conditional on presence (deleting something absent is a no-op).
    present=c.execute("SELECT 1 FROM oos_units WHERE unit=?", (asset,)).fetchone()
    if present:
        c.execute("DELETE FROM oos_units WHERE unit=?", (asset,))
    c.execute("INSERT INTO rts_log(shift_id,unit,removed_at) VALUES (?,?,?)",
              (shift_id,asset,datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
    c.execute("UPDATE v3_breakdown_state SET rts_logged=1 WHERE wo_number=?", (wo,))
    return True

def sync(db, rows, selected, shift_id, allowed_new_wos=None):
    """Apply same-WO status updates. Unselected staff can update tracked WOs only.

    `rows` are raw parsed Cetaris rows (pre select_records filtering) that
    must carry an "rfr" field classifying breakdown WOs.

    `allowed_new_wos` (2026-10-24, S1-S5 ownership engine): when given, a WO
    NOT already tracked may only start being tracked here if its number is
    in this set (i.e. v3_split.py's ownership engine attributed it to THIS
    shift). A breakdown opened by a different shift must never start OOS
    tracking here just because one of its labor rows' technician also
    appears in this file. Already-tracked WOs are exempt -- OOS/RTS state
    is shared infrastructure, so a status update (e.g. closing it out) must
    still flow through regardless of which shift is doing the updating.
    """
    ensure_tables(db)
    priority={"in process":1,"completed":2,"closed":3}
    # Real Cetaris exports at this facility don't use a literal "Completed"
    # status for breakdown WOs -- a tech finishing the repair puts it at
    # "Manager Review Required" (pending admin sign-off/closure), which is
    # exactly the "tech says done, needs an RTS/keep-OOS call" moment this
    # module's `completed` bucket exists for. Alias known real-world
    # synonyms onto the canonical three states so pending()/decide() (which
    # key off the literal string "completed") keep working unchanged.
    status_aliases={"manager review required":"completed"}
    selected_wos={}
    with db._conn() as c:
        tracked={r[0] for r in c.execute("SELECT wo_number FROM v3_breakdown_state")}
    for r in rows:
        if r.get("rfr","").strip().casefold()!="breakdown": continue
        wo=str(r.get("wo_number","")).strip()
        raw_status=str(r.get("status","")).strip().casefold()
        status=status_aliases.get(raw_status,raw_status)
        asset=str(r.get("asset_id","")).strip()
        if not wo or not asset or status not in priority: continue
        if r.get("tech_name") not in selected and wo not in tracked: continue
        if wo not in tracked and allowed_new_wos is not None and wo not in allowed_new_wos: continue
        previous=selected_wos.get(wo)
        if previous is None or priority[status]>priority[previous[0]]:
            selected_wos[wo]=(status,asset)
    updated=0
    with db._conn() as c:
        for wo,(status,asset) in selected_wos.items():
            old=c.execute("SELECT * FROM v3_breakdown_state WHERE wo_number=?", (wo,)).fetchone()
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

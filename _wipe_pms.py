"""Wipe pending_pms + pm_files for the active shift so we can test a fresh upload."""
import sqlite3, pathlib, os

DB = pathlib.Path(__file__).parent / "dbs" / "ind3.db"
con = sqlite3.connect(str(DB))
shift = con.execute("SELECT id,shift_type,lead_name FROM shifts WHERE is_active=1 ORDER BY id DESC LIMIT 1").fetchone()
if not shift:
    print("No active shift found.")
    con.close()
    raise SystemExit(0)
sid, stype, lead = shift
pms  = con.execute("DELETE FROM pending_pms WHERE shift_id=?", (sid,)).rowcount
fils = con.execute("DELETE FROM pm_files   WHERE shift_id=?", (sid,)).rowcount
con.commit()
con.close()

# Also remove uploaded files from disk
pm_dir = pathlib.Path(__file__).parent / "pm_uploads"
removed = 0
if pm_dir.exists():
    for f in pm_dir.glob(f"{sid}_*"):
        f.unlink()
        removed += 1

print(f"Shift {sid} ({stype} | {lead})")
print(f"  pending_pms  deleted : {pms}")
print(f"  pm_files     deleted : {fils}")
print(f"  disk files   removed : {removed}")
print("Done. Ready for a fresh PM upload test.")

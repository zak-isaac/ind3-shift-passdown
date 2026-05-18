"""
Surgical cleanup of the active shift (ID=16, S5 2026-05-17, Stanley A).
Clears: entries, reports, rts_log, tech_done, pm_wos, pm_files, oos_units
Keeps:  the shift row itself + ALL other shifts and their data
"""
import sqlite3, shutil
from datetime import datetime

DB_PATH  = "dbs/ind3.db"
BAK_PATH = DB_PATH + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

shutil.copy2(DB_PATH, BAK_PATH)
print(f"Backup -> {BAK_PATH}")

ACTIVE_SHIFT_ID = 16

with sqlite3.connect(DB_PATH) as c:
    # Tables scoped to shift_id
    shift_tables = ["entries", "reports", "rts_log", "tech_done", "pm_wos", "pm_files", "pending_pms", "happenings_files"]
    for t in shift_tables:
        try:
            n = c.execute(f"DELETE FROM {t} WHERE shift_id=?", (ACTIVE_SHIFT_ID,)).rowcount
            print(f"  {t}: deleted {n} rows")
        except Exception as e:
            print(f"  {t}: skipped ({e})")

    # OOS units are global — clear all (they were added during testing)
    n = c.execute("DELETE FROM oos_units").rowcount
    print(f"  oos_units (global): deleted {n} rows")

    # Reset auto-increment for entries and reports so IDs stay tidy
    # (optional — only if both tables are now empty across ALL shifts)
    total_entries = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
    total_reports = c.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
    print(f"\n  Remaining entries across all shifts: {total_entries}")
    print(f"  Remaining reports across all shifts: {total_reports}")

    # Verify the shift row is still intact
    shift = c.execute("SELECT * FROM shifts WHERE id=?", (ACTIVE_SHIFT_ID,)).fetchone()
    print(f"\n  Shift row preserved: {dict(zip([d[0] for d in c.execute('PRAGMA table_info(shifts)').fetchall()], shift))}")

    # Show other shifts are untouched
    others = c.execute("SELECT id, shift_type, date, lead_name, is_active FROM shifts WHERE id!=?", (ACTIVE_SHIFT_ID,)).fetchall()
    print(f"\n  Other shifts preserved ({len(others)}):")
    for s in others:
        print(f"    ID={s[0]} {s[1]} {s[2]} {s[3]} active={s[4]}")

print("\nDone — active shift is clean and ready for real use.")

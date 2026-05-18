"""Reset all shift/entry test data. Keeps settings (email config, associates).
Run from project root: python _reset_data.py
"""
import db, sqlite3, shutil, os
from datetime import datetime

DB_PATH  = str(db._DB_PATH)
BAK_PATH = DB_PATH + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# ── Safety backup ──────────────────────────────────────────────────────────────
shutil.copy2(DB_PATH, BAK_PATH)
print(f"Backup saved -> {BAK_PATH}")

# ── Wipe ───────────────────────────────────────────────────────────────────────
with sqlite3.connect(DB_PATH) as c:
    tables_to_clear = [
        "entries",
        "reports",
        "oos_units",
        "tech_done",
        "rts_log",
        "pm_wos",
        "shifts",
    ]
    for t in tables_to_clear:
        n = c.execute(f"DELETE FROM {t}").rowcount
        print(f"  Cleared {t}: {n} rows deleted")

    # Reset auto-increment counters so IDs start from 1
    seqs = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_sequence"
    ).fetchall()]
    for t in tables_to_clear:
        if t in seqs:
            c.execute("DELETE FROM sqlite_sequence WHERE name=?", (t,))
            print(f"  Reset auto-increment: {t}")

    # Verify settings are untouched
    settings = c.execute("SELECT key, value FROM settings").fetchall()
    print(f"\n  Settings preserved ({len(settings)} keys):")
    for k, v in settings:
        print(f"    {k} = {v[:60] if v else '(empty)'}")

print("\nDone — DB is clean and ready for real use.")
print(f"Rollback anytime: copy '{BAK_PATH}' back to '{DB_PATH}'")

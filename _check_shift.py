import db, sqlite3

c = sqlite3.connect(str(db._DB_PATH))
c.row_factory = sqlite3.Row

print("=== ALL SHIFTS ===")
shifts = c.execute("SELECT * FROM shifts ORDER BY id").fetchall()
for s in shifts:
    d = dict(s)
    print(f"  ID={d['id']} {d['shift_type']} {d['date']} lead={d['lead_name']} active={d['is_active']}")

print("\n=== ACTIVE SHIFT ===")
active = c.execute("SELECT * FROM shifts WHERE is_active=1").fetchone()
if active:
    d = dict(active)
    sid = d['id']
    print(f"  Shift {sid} | {d['shift_type']} | {d['date']} | {d['lead_name']}")
    tables = [
        ("entries",  "SELECT id,tech_name,unit,wo_number,source,issue FROM entries WHERE shift_id=?"),
        ("tech_done","SELECT * FROM tech_done WHERE shift_id=?"),
        ("rts_log",  "SELECT * FROM rts_log WHERE shift_id=?"),
        ("pm_wos",   "SELECT * FROM pm_wos WHERE shift_id=?"),
        ("reports",  "SELECT id,generated_at FROM reports WHERE shift_id=?"),
    ]
    for name, q in tables:
        rows = c.execute(q, (sid,)).fetchall()
        print(f"\n  {name} ({len(rows)}):")
        for r in rows:
            print(f"    {dict(r)}")
    oos = c.execute("SELECT * FROM oos_units").fetchall()
    print(f"\n  oos_units (global) ({len(oos)}): {[dict(o) for o in oos]}")
else:
    print("  No active shift found.")
    print("\n  (All entries across all shifts:)")
    rows = c.execute("SELECT id,shift_id,tech_name,issue FROM entries ORDER BY id").fetchall()
    for r in rows:
        d = dict(r)
        print(f"    [{d['id']}] shift={d['shift_id']} {d['tech_name']} | {d['issue'][:55]}")

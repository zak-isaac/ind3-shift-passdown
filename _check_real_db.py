import sqlite3

c = sqlite3.connect("dbs/ind3.db")
c.row_factory = sqlite3.Row

print("Tables:", [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()])

shifts = c.execute("SELECT * FROM shifts ORDER BY id DESC LIMIT 10").fetchall()
print("\nShifts:")
for s in shifts:
    d = dict(s)
    print(f"  ID={d['id']} {d['shift_type']} {d['date']} lead={d['lead_name']} active={d['is_active']}")

active = c.execute("SELECT * FROM shifts WHERE is_active=1").fetchone()
if active:
    sid = dict(active)["id"]
    print(f"\nActive shift: {sid}")
    entries = c.execute("SELECT id,tech_name,unit,wo_number,source,excluded,issue FROM entries WHERE shift_id=?", (sid,)).fetchall()
    print(f"Entries ({len(entries)}):")
    for e in entries:
        d = dict(e)
        print(f"  [{d['id']}] {d['tech_name']:<16} {d['unit']:<10} WO:{d['wo_number']:<10} excl:{d['excluded']} | {d['issue'][:55]}")
    oos = c.execute("SELECT * FROM oos_units").fetchall()
    print(f"OOS ({len(oos)}): {[dict(o) for o in oos]}")
    rts = c.execute("SELECT * FROM rts_log WHERE shift_id=?", (sid,)).fetchall()
    print(f"rts_log ({len(rts)})")
    td = c.execute("SELECT * FROM tech_done WHERE shift_id=?", (sid,)).fetchall()
    print(f"tech_done ({len(td)})")
    pm = c.execute("SELECT * FROM pm_wos WHERE shift_id=?", (sid,)).fetchall()
    print(f"pm_wos ({len(pm)})")
    rpts = c.execute("SELECT id,generated_at FROM reports WHERE shift_id=?", (sid,)).fetchall()
    print(f"reports ({len(rpts)}): {[dict(r) for r in rpts]}")
else:
    print("\nNo active shift.")

"""
Exclude any existing entries that Cetaris would now filter as Manager Tasks.
These slipped through before the parser filter was deployed.
Targets: unit = BLDG or BLDG.* (building maintenance = manager task in Cetaris)
"""
import sqlite3, shutil
from datetime import datetime

DB   = "dbs/ind3.db"
BAK  = DB + f".bak_exclude_mgr_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
shutil.copy2(DB, BAK)
print(f"Backup -> {BAK}")

c = sqlite3.connect(DB)
c.row_factory = sqlite3.Row

candidates = c.execute("""
    SELECT id, shift_id, unit, wo_number, substr(issue,1,90) as preview
    FROM   entries
    WHERE  (upper(unit) = 'BLDG' OR upper(unit) LIKE 'BLDG.%')
    AND    excluded = 0
""").fetchall()

if not candidates:
    print("Nothing to exclude — all BLDG entries already excluded or none exist.")
else:
    print(f"Found {len(candidates)} entry(s) to exclude:")
    for r in candidates:
        print(f"  id={r['id']} shift={r['shift_id']} unit={r['unit']} wo={r['wo_number']}")
        print(f"    {r['preview']}")

    ids = [r["id"] for r in candidates]
    c.execute(f"UPDATE entries SET excluded=1 WHERE id IN ({','.join('?'*len(ids))})", ids)
    c.commit()
    print(f"\nExcluded {len(ids)} entries. Done.")

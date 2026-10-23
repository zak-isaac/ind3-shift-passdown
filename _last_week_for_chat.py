import sqlite3
import wo_consolidator as wc

SHIFT_IDS = [68, 70, 71, 72, 73, 74, 75, 77, 78, 79]  # 2026-09-14 through 2026-09-20

conn = sqlite3.connect("file:dbs/ind3.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

lines = []
for sid in SHIFT_IDS:
    shift = conn.execute("SELECT * FROM shifts WHERE id=?", (sid,)).fetchone()
    if not shift:
        continue
    entries = [dict(r) for r in conn.execute(
        "SELECT * FROM entries WHERE shift_id=? ORDER BY created_at ASC", (sid,)
    ).fetchall()]
    result = wc.consolidate_entries(entries)
    for cwo in result.consolidated:
        if not wc.has_real_remarks(cwo):
            continue
        remark_text = " ".join(cwo.all_remarks)
        lines.append(f"[{shift['date']} {shift['shift_type']}] {cwo.asset_id}: {remark_text}")

print(f"Total real-remark WOs: {len(lines)}")
print(f"Approx chars: {sum(len(l) for l in lines)}")

with open("_last_week_for_chat.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

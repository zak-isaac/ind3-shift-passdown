"""Read-only, FULL-DETAIL review: every WO across this week's 8 shifts,
untruncated, through the CURRENT deterministic pipeline (AI disabled).
Never writes to the DB -- opened read-only (file:...?mode=ro).
Writes to _week_review_output.txt instead of stdout to avoid terminal
capture truncation on long lines / long total output.
"""
import sqlite3
import sys

import ai_rewriter
import wo_consolidator as wc

SHIFT_IDS = [68, 70, 71, 72, 73, 74, 75, 76]  # 2026-09-14 through 2026-09-19

sys.stdout = open("_week_review_output.txt", "w", encoding="utf-8")

conn = sqlite3.connect("file:dbs/ind3.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row


def get_entries(shift_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM entries WHERE shift_id=? ORDER BY created_at ASC", (shift_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_shift(shift_id: int) -> dict:
    row = conn.execute("SELECT * FROM shifts WHERE id=?", (shift_id,)).fetchone()
    return dict(row) if row else {}


for shift_id in SHIFT_IDS:
    shift = get_shift(shift_id)
    if not shift:
        continue
    entries = get_entries(shift_id)
    result = wc.consolidate_entries(entries)
    print(f"\n{'#' * 78}")
    print(f"# SHIFT {shift_id}  |  {shift['date']} {shift['shift_type']}  |  lead: {shift['lead_name']}  |  {len(result.consolidated)} WOs")
    print("#" * 78)

    for cwo in result.consolidated:
        print(f"\n{'-' * 78}")
        print(f"WO #{cwo.wo_number}  |  Asset: {cwo.asset_id or '(none)'}  |  Priority: {cwo.priority}")
        print(f"Techs: {', '.join(cwo.techs) if cwo.techs else '(none)'}")
        print(f"Entries in group: {cwo.entry_count}  |  Entry IDs: {cwo.raw_entry_ids}")
        print(f"First logged: {cwo.first_logged}  |  Last logged: {cwo.last_logged}")
        print(f"Conflicting: {cwo.conflicting}  |  Possible duplicate: {cwo.possible_duplicate}")

        if not wc.has_real_remarks(cwo):
            print("Remarks: NONE -- honest placeholder used, AI/cleanup skipped entirely")
            print(f"Placeholder summary: {cwo.summary}")
            continue

        print(f"\nRaw remarks ({len(cwo.all_remarks)}):")
        for i, r in enumerate(cwo.all_remarks, 1):
            print(f"  {i}. {r}")

        raw_input = ai_rewriter.build_input(cwo.asset_id, cwo.all_remarks)
        output = ai_rewriter.rewrite(raw_input)
        print(f"\nFinal report output:\n  {output}")

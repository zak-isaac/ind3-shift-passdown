"""Read-only, combined view: RAW entries -> FINAL report output, for
last week's shifts (9/14 through 9/20). Runs the CURRENT deterministic
pipeline (AI disabled). Writes to file to avoid terminal truncation.
"""
import json
import sqlite3
import sys

import ai_rewriter
import wo_consolidator as wc

SHIFT_IDS = [68, 70, 71, 72, 73, 74, 75, 77, 78, 79]  # 2026-09-14 through 2026-09-20

conn = sqlite3.connect("file:dbs/ind3.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

sys.stdout = open("_raw_to_report_lastweek.txt", "w", encoding="utf-8")


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
    print(f"# SHIFT {shift_id}  |  {shift['date']} {shift['shift_type']}  |  lead: {shift['lead_name']}")
    print("#" * 78)

    for cwo in result.consolidated:
        matching_raw = [e for e in entries if str(e.get("wo_number")) == str(cwo.wo_number)]
        flags = []
        if cwo.conflicting:
            flags.append("CONFLICTING")
        if cwo.possible_duplicate:
            flags.append("POSSIBLE_DUP")
        flag_str = f"  [{', '.join(flags)}]" if flags else ""

        print(f"\n{'-' * 78}")
        print(f"WO #{cwo.wo_number}  Asset: {cwo.asset_id or '(none)'}  Techs: {cwo.techs}{flag_str}")

        print("RAW entries:")
        for e in matching_raw:
            excluded = " [EXCLUDED -- not in report]" if e.get("excluded") else ""
            try:
                remarks = json.loads(e.get("raw_remarks") or "[]")
            except (json.JSONDecodeError, TypeError):
                remarks = []
            print(f"  id={e['id']} tech={e['tech_name']!r} @{e['created_at']}{excluded}")
            for r in remarks:
                print(f"    - {r}")

        if not wc.has_real_remarks(cwo):
            print(f"\n>>> REPORT OUTPUT: (no real remarks -- honest placeholder, AI/cleanup skipped)\n    {cwo.summary}")
            continue

        raw_input = ai_rewriter.build_input(cwo.asset_id, cwo.all_remarks)
        output = ai_rewriter.rewrite(raw_input)
        print(f"\n>>> REPORT OUTPUT:\n    {output}")

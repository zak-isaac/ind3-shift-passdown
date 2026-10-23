"""Compare CURRENT deterministic output vs gemma3n-e2b (run through the
SAME production guard chain via ai_rewriter.rewrite(), just with _USE_AI
and _AVAILABLE_MODELS monkeypatched for this process only -- the actual
files/production server are untouched). Read-only against the DB.
"""
import sqlite3
import sys

import ai_rewriter
import wo_consolidator as wc

SHIFT_IDS = [68, 70, 71, 72, 73, 74]  # 2026-09-14 through 2026-09-17, per user request

conn = sqlite3.connect("file:dbs/ind3.db?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

sys.stdout = open("_gemma3n_comparison_lastweek.txt", "w", encoding="utf-8")


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
    print(f"# SHIFT {shift_id}  |  {shift['date']} {shift['shift_type']}")
    print("#" * 78)

    for cwo in result.consolidated:
        if not wc.has_real_remarks(cwo):
            continue

        raw_input = ai_rewriter.build_input(cwo.asset_id, cwo.all_remarks)

        ai_rewriter._USE_AI = False
        deterministic_out = ai_rewriter.rewrite(raw_input)

        ai_rewriter._USE_AI = True
        ai_rewriter._AVAILABLE_MODELS = ["gemma3n-e2b"]
        gemma_out = ai_rewriter.rewrite(raw_input)

        print(f"\n{'-' * 78}")
        print(f"WO #{cwo.wo_number}  Asset: {cwo.asset_id}")
        print("RAW:", " | ".join(cwo.all_remarks))
        print("DETERMINISTIC:", deterministic_out)
        print("GEMMA3N-E2B:  ", gemma_out)
        if deterministic_out.strip() != gemma_out.strip():
            print("  >>> DIFFERS")
        sys.stdout.flush()  # so progress is visible in the file while running long

ai_rewriter._USE_AI = False

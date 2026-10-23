import time
import db
import ai_rewriter
import registry
from wo_consolidator import consolidate_entries, consolidated_to_happenings, has_real_remarks

db.set_db_path(registry.db_path_for("IND3"))
ai_rewriter.check_ollama()
print(f"[verify] Model: {ai_rewriter._MODEL}\n", flush=True)

shift_id = 74  # today's shift, the one the user showed the report from
db_entries = db.get_entries(shift_id)
co_result = consolidate_entries(db_entries)
co_happenings = consolidated_to_happenings(co_result)

for h in co_happenings:
    wo = h["wo_number"]
    title = h["title"]
    cwo_match = next((c for c in co_result.consolidated if c.wo_number == wo), None)
    raw = [r for r in (cwo_match.all_remarks if cwo_match else []) if r and r.strip()]

    t0 = time.time()
    if not cwo_match or not has_real_remarks(cwo_match):
        result = h.get("paragraph") or title
        mode = "NO-AI"
    else:
        ai_input = ai_rewriter.build_input(title, raw)
        result = ai_rewriter.rewrite_batch([ai_input])[0]
        mode = "AI"
    elapsed = time.time() - t0

    print(f"{title} (WO#{wo}) [{mode}, {elapsed:.1f}s]", flush=True)
    print(f"  RAW:    {raw}", flush=True)
    print(f"  OUTPUT: {result}\n", flush=True)

print("[verify] DONE", flush=True)

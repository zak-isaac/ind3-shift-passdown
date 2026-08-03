"""
test_report.py -- generate a passdown HTML from passdowns.db and open it.
Run from IND3-Passdown-Server directory.
"""
import sys, os, asyncio, webbrowser, tempfile, sqlite3, json

# Make sure imports resolve from the right place
sys.path.insert(0, os.path.dirname(__file__))

import ai_rewriter
import formatter
from wo_consolidator import consolidate_entries
from report_models import RewrittenHappening, ReportData, OosUnit

DB_PATH  = r'C:\Users\ins0002\Documents\puppy_workspace\IND3-Passdown-Server\dbs\ind3.db'
SHIFT_ID = 30  # August 1 2026, Stanley A -- real multi-tech WOs

# ── Load data from DB ─────────────────────────────────────────────────────────
db = sqlite3.connect(DB_PATH)
db.row_factory = sqlite3.Row

shift = db.execute(
    "SELECT * FROM shifts WHERE id=?", (SHIFT_ID,)
).fetchone()

if not shift:
    print(f"Shift {SHIFT_ID} not found")
    sys.exit(1)

shift_id = shift["id"]
print(f"Using shift {shift_id}  |  {shift['date']}  |  {shift['shift_type']}  |  Lead: {shift['lead_name']}")

entries = db.execute(
    "SELECT * FROM entries WHERE shift_id = ?", (shift_id,)
).fetchall()
entries = [dict(e) for e in entries]
print(f"Found {len(entries)} entries")

oos_rows = []  # oos_units table schema differs in old DB -- skip


# ── Run pipeline ──────────────────────────────────────────────────────────────
co_result = consolidate_entries(entries)
print(f"Consolidated to {len(co_result.consolidated)} WOs + {len(co_result.passthrough)} passthrough")

# Build AI inputs per-tech for multi-tech WOs, single for the rest
from collections import defaultdict
ai_inputs   = []
input_meta  = []  # (happening_idx, tech_name)
co_happenings = []

for cwo in co_result.consolidated:
    title = cwo.asset_id or cwo.wo_number
    co_happenings.append({
        "title": title,
        "wo_number": cwo.wo_number,
        "tech_name": cwo.techs[0] if cwo.techs else "",
        "paragraph": cwo.summary,
        "raw_remark": cwo.summary,
    })

for pt in co_result.passthrough:
    raw = json.loads(pt.get("raw_remarks") or "[]") or [pt.get("issue", "")]
    co_happenings.append({
        "title": pt.get("unit", "Unknown"),
        "wo_number": pt.get("wo_number", ""),
        "tech_name": pt.get("tech_name", ""),
        "paragraph": raw[0] if raw else "",
        "raw_remark": raw[0] if raw else "",
    })

for h_idx, h in enumerate(co_happenings):
    cwo_match = next((c for c in co_result.consolidated if c.wo_number == h["wo_number"]), None)
    if cwo_match and len(cwo_match.tech_remarks) > 1:
        for t_name, remark in cwo_match.tech_remarks:
            if ai_rewriter.is_non_technical(remark):
                continue
            ai_inputs.append(ai_rewriter.build_input(h["title"], [remark]))
            input_meta.append((h_idx, t_name))
    else:
        raw = []
        if cwo_match:
            raw = [r for r in cwo_match.all_remarks if r and r.strip()
                   and not ai_rewriter.is_non_technical(r)]
        if not raw:
            raw = [h.get("paragraph") or h.get("raw_remark") or h["title"]]
        if ai_rewriter.is_non_technical(" ".join(raw)):
            input_meta.append((h_idx, "__SKIP__"))
            ai_inputs.append("")
            continue
        ai_inputs.append(ai_rewriter.build_input(h["title"], raw))
        input_meta.append((h_idx, h.get("tech_name", "")))

print(f"Sending {len(ai_inputs)} remarks to {ai_rewriter._MODEL}...")
rewritten_flat = ai_rewriter.rewrite_batch(ai_inputs)

groups: dict = defaultdict(list)
for (h_idx, t_name), para in zip(input_meta, rewritten_flat):
    groups[h_idx].append((t_name, para))

happenings = []
for h_idx, h in enumerate(co_happenings):
    pts = groups[h_idx]
    if not pts or all(t == "__SKIP__" or not p.strip() for t, p in pts):
        continue
    if len(pts) > 1:
        clean_pts = [(t, p) for t, p in pts if t != "__SKIP__" and p.strip()]
        if not clean_pts:
            continue
        happenings.append(RewrittenHappening(
            title=h["title"], paragraph=clean_pts[0][1],
            wo_number=h["wo_number"], tech_name=clean_pts[0][0],
            sub_points=clean_pts,
        ))
    else:
        happenings.append(RewrittenHappening(
            title=h["title"],
            paragraph=pts[0][1] if pts else h.get("paragraph",""),
            wo_number=h["wo_number"],
            tech_name=h.get("tech_name",""),
            sub_points=[],
        ))

oos = [OosUnit(unit=r["unit"]) for r in oos_rows]

report = ReportData(
    date        = shift["date"],
    shift_type  = shift["shift_type"],
    lead_name   = shift["lead_name"],
    dc_number   = "8972",
    happenings  = happenings,
    oos         = oos,
)

# ── Render and open ───────────────────────────────────────────────────────────
html_body = formatter.format_html_preview(report)

# Wrap in a full HTML page
html = f"""<!DOCTYPE html>
<html><head><meta charset='utf-8'>
<title>Passdown Test - {report.date}</title>
<meta name='viewport' content='width=device-width,initial-scale=1'>
</head><body style='background:#f3f4f6;font-family:Calibri,Arial,sans-serif;margin:0;padding:20px;'>
{html_body}
</body></html>"""
out  = os.path.join(tempfile.gettempdir(), "passdown_test.html")
with open(out, "w", encoding="utf-8") as f:
    f.write(html)

print(f"\nReport saved -> {out}")
webbrowser.open(f"file:///{out}")
print("Opened in browser!")

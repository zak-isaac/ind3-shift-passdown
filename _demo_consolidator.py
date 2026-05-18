"""
Generate a self-contained HTML demo showing WO consolidation before vs after.
Run: python _demo_consolidator.py  → opens demo.html in the browser
"""
import sys, os, json, webbrowser
sys.path.insert(0, os.path.dirname(__file__))
from wo_consolidator import consolidate_entries, consolidated_to_happenings

# ── Realistic fake shift data ─────────────────────────────────────────────────
# Two WOs that multiple techs touched, plus one manual no-WO entry,
# plus one entry that was excluded by the MST

FAKE_ENTRIES = [
    # WO #27771902 — 3 techs, same conveyor motor issue
    {
        "id": 1, "shift_id": 42, "tech_name": "Isaac S", "unit": "M2785",
        "issue": "WO #27771902 — Motor tripping thermal breaker",
        "action": "", "result": "", "created_at": "2026-05-17 06:12:00",
        "priority": "high", "wo_number": "27771902", "excluded": 0, "source": "cmms",
        "raw_remarks": json.dumps([
            "Motor tripping thermal breaker on startup. Checked amp draw — reading 28A on phase 2 vs 18A normal."
        ]),
    },
    {
        "id": 2, "shift_id": 42, "tech_name": "Stanley A", "unit": "M2785",
        "issue": "WO #27771902 — Phase 2 capacitor suspected",
        "action": "", "result": "", "created_at": "2026-05-17 07:45:00",
        "priority": "high", "wo_number": "27771902", "excluded": 0, "source": "cmms",
        "raw_remarks": json.dumps([
            "Verified Isaac's amp reading. Traced to failing run capacitor on phase 2. Capacitor tested at 18uF vs rated 35uF."
        ]),
    },
    {
        "id": 3, "shift_id": 42, "tech_name": "Marcus T", "unit": "M2785",
        "issue": "WO #27771902 — Capacitor replaced, unit RTS",
        "action": "", "result": "", "created_at": "2026-05-17 09:10:00",
        "priority": "monitor", "wo_number": "27771902", "excluded": 0, "source": "cmms",
        "raw_remarks": json.dumps([
            "Replaced run capacitor with 35uF/370VAC unit from parts room. Motor tested — amp draw normal at 19A all phases. Unit returned to service."
        ]),
    },

    # WO #30045812 — 2 techs, baler hydraulic issue still open
    {
        "id": 4, "shift_id": 42, "tech_name": "Isaac S", "unit": "BALER-01",
        "issue": "WO #30045812 — Baler hydraulic leak",
        "action": "", "result": "", "created_at": "2026-05-17 08:00:00",
        "priority": "critical", "wo_number": "30045812", "excluded": 0, "source": "cmms",
        "raw_remarks": json.dumps([
            "Hydraulic leak identified at the cylinder rod seal. Fluid pooling under unit. Tagged OOS."
        ]),
    },
    {
        "id": 5, "shift_id": 42, "tech_name": "Stanley A", "unit": "BALER-01",
        "issue": "WO #30045812 — Seal kit ordered",
        "action": "", "result": "", "created_at": "2026-05-17 10:30:00",
        "priority": "critical", "wo_number": "30045812", "excluded": 0, "source": "cmms",
        "raw_remarks": json.dumps([
            "Seal kit part #HYD-4412 ordered from MaintenanceOne. ETA next shift. Unit remains OOS pending parts."
        ]),
    },

    # Manual entry — no WO number (a vendor note the MST typed in)
    {
        "id": 6, "shift_id": 42, "tech_name": "Stanley A", "unit": "Vendor",
        "issue": "Crown lift technician on-site to service WAV-15 battery",
        "action": "Escorted vendor", "result": "Service completed",
        "created_at": "2026-05-17 11:00:00",
        "priority": "monitor", "wo_number": "", "excluded": 0, "source": "manual",
        "raw_remarks": "[]",
    },

    # Excluded entry — MST clicked exclude on this one (bad duplicate upload)
    {
        "id": 7, "shift_id": 42, "tech_name": "Isaac S", "unit": "M2785",
        "issue": "WO #27771902 — accidental duplicate upload",
        "action": "", "result": "", "created_at": "2026-05-17 06:11:00",
        "priority": "monitor", "wo_number": "27771902", "excluded": 1, "source": "cmms",
        "raw_remarks": json.dumps(["DUPLICATE - ignore"]),
    },
]


# ── Run consolidation ─────────────────────────────────────────────────────────
result = consolidate_entries(FAKE_ENTRIES)
happenings = consolidated_to_happenings(result)


# ── Build HTML ────────────────────────────────────────────────────────────────
BLUE    = "#0053e2"
SPARK   = "#ffc220"
RED     = "#ea1100"
GREEN   = "#2a8703"
DARK    = "#1e293b"
GRAY_BG = "#f8fafc"

def entry_card(e, excluded=False):
    badge_color = RED if excluded else "#64748b"
    badge_text  = "EXCLUDED" if excluded else f"ID #{e['id']}"
    prio_colors = {"critical": RED, "high": "#ea580c", "monitor": "#64748b"}
    prio_c = prio_colors.get(e.get("priority","monitor"), "#64748b")
    remarks = json.loads(e.get("raw_remarks","[]"))
    remark_html = f'<div style="margin-top:6px;font-style:italic;color:#475569;font-size:13px;">"{remarks[0]}"</div>' if remarks else ""
    excl_style = "opacity:0.45;filter:grayscale(1);" if excluded else ""
    return f"""
    <div style="border:1px solid #e2e8f0;border-radius:10px;padding:14px 16px;
                margin-bottom:10px;background:white;{excl_style}position:relative;">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px;">
        <span style="background:{badge_color};color:white;font-size:11px;font-weight:700;
                     padding:2px 8px;border-radius:999px;">{badge_text}</span>
        <span style="background:{prio_c};color:white;font-size:11px;font-weight:600;
                     padding:2px 8px;border-radius:999px;">{e.get('priority','').upper()}</span>
        <span style="font-size:11px;color:#94a3b8;">{e['created_at']}</span>
        {"<span style='margin-left:auto;color:" + RED + ";font-weight:700;font-size:12px;'>✕ EXCLUDED BY MST</span>" if excluded else ""}
      </div>
      <div style="font-weight:700;color:{DARK};margin-bottom:2px;">
        👤 {e['tech_name']} &nbsp;·&nbsp; 📦 {e['unit']}
        {"&nbsp;·&nbsp; 🔖 WO #" + e['wo_number'] if e['wo_number'] else ""}
      </div>
      <div style="color:#334155;font-size:14px;">{e['issue']}</div>
      {remark_html}
    </div>"""


def consolidated_card(wo):
    prio_colors = {"critical": RED, "high": "#ea580c", "monitor": GREEN}
    prio_c = prio_colors.get(wo.priority, "#64748b")
    techs_html = "".join(
        f'<span style="background:#dbeafe;color:{BLUE};font-size:12px;font-weight:600;'
        f'padding:3px 10px;border-radius:999px;">{t}</span>'
        for t in wo.techs
    )
    remarks_html = "".join(
        f'<li style="margin-bottom:6px;color:#334155;font-size:13px;">{r}</li>'
        for r in wo.all_remarks
    )
    conflict_html = ""
    if wo.conflicting:
        conflict_html = f"""
        <div style="background:#fef3c7;border:1px solid #fcd34d;border-radius:8px;
                    padding:8px 12px;margin-top:10px;font-size:13px;color:#92400e;">
          ⚠️ <strong>Conflicting notes detected</strong> — verify final status with techs
        </div>"""
    return f"""
    <div style="border:2px solid {BLUE};border-radius:12px;padding:18px 20px;
                margin-bottom:14px;background:white;
                box-shadow:0 4px 16px rgba(0,83,226,0.08);">
      <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap;">
        <span style="background:{BLUE};color:white;font-size:13px;font-weight:800;
                     padding:4px 12px;border-radius:999px;">WO #{wo.wo_number}</span>
        <span style="background:{prio_c};color:white;font-size:11px;font-weight:700;
                     padding:3px 10px;border-radius:999px;">{wo.priority.upper()}</span>
        <span style="background:#f1f5f9;color:#64748b;font-size:11px;padding:3px 10px;border-radius:999px;">
          {wo.entry_count} entries merged
        </span>
        <span style="background:#f1f5f9;color:#64748b;font-size:11px;padding:3px 10px;border-radius:999px;">
          🕐 {wo.first_logged} → {wo.last_logged}
        </span>
      </div>

      <div style="margin-bottom:10px;">
        <span style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;
                     letter-spacing:0.05em;">📦 Asset</span>
        <span style="font-weight:700;color:{DARK};margin-left:8px;">{wo.asset_id}</span>
      </div>

      <div style="margin-bottom:12px;">
        <span style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;
                     letter-spacing:0.05em;">👥 Techs Involved</span>
        <div style="display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;">{techs_html}</div>
      </div>

      <div style="margin-bottom:10px;">
        <span style="font-size:12px;font-weight:700;color:#64748b;text-transform:uppercase;
                     letter-spacing:0.05em;">📋 Individual Remarks (chronological)</span>
        <ul style="margin:8px 0 0;padding-left:18px;">{remarks_html}</ul>
      </div>

      <div style="background:{GRAY_BG};border-left:4px solid {BLUE};border-radius:0 8px 8px 0;
                  padding:12px 16px;margin-top:10px;">
        <span style="font-size:12px;font-weight:700;color:{BLUE};text-transform:uppercase;
                     letter-spacing:0.05em;">✅ Consolidated Summary (what goes in the report)</span>
        <p style="margin:8px 0 0;color:{DARK};font-size:14px;line-height:1.7;">{wo.summary}</p>
      </div>
      {conflict_html}
    </div>"""


# Build before section (raw entries, all 7)
before_html = "".join(
    entry_card(e, excluded=bool(e.get("excluded")))
    for e in sorted(FAKE_ENTRIES, key=lambda x: x["created_at"])
)

# Build after section (consolidated)
after_consolidated = "".join(consolidated_card(wo) for wo in result.consolidated)
after_passthrough  = "".join(entry_card(e) for e in result.passthrough)

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WO Consolidation Demo — IND3 Passdown</title>
<style>
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Segoe UI',system-ui,sans-serif; background:{GRAY_BG}; color:{DARK}; }}
  .hero {{ background:{BLUE}; color:white; padding:32px 40px; }}
  .hero h1 {{ font-size:24px; font-weight:900; }}
  .hero p {{ font-size:14px; opacity:0.85; margin-top:6px; }}
  .container {{ max-width:1100px; margin:0 auto; padding:32px 20px; }}
  .cols {{ display:grid; grid-template-columns:1fr 1fr; gap:32px; }}
  .col-head {{ font-size:14px; font-weight:800; text-transform:uppercase;
               letter-spacing:0.08em; margin-bottom:16px; padding-bottom:10px;
               border-bottom:3px solid; }}
  .before .col-head {{ color:{RED}; border-color:{RED}; }}
  .after  .col-head {{ color:{BLUE}; border-color:{BLUE}; }}
  .stat-bar {{ display:flex; gap:16px; margin-bottom:28px; flex-wrap:wrap; }}
  .stat {{ background:white; border-radius:10px; padding:14px 20px;
           border:1px solid #e2e8f0; flex:1; min-width:160px; }}
  .stat .num {{ font-size:28px; font-weight:900; }}
  .stat .lbl {{ font-size:12px; color:#64748b; margin-top:2px; }}
  .arrow {{ display:flex; align-items:center; justify-content:center;
            font-size:40px; color:{BLUE}; padding-top:60px; }}
  .section-label {{ font-size:12px; font-weight:700; color:#64748b;
                    text-transform:uppercase; letter-spacing:0.08em;
                    margin:16px 0 8px; }}
  @media(max-width:700px){{ .cols{{grid-template-columns:1fr;}} .arrow{{padding:0;transform:rotate(90deg);}} }}
</style>
</head>
<body>
<div class="hero">
  <h1>WO Consolidation — Live Demo</h1>
  <p>IND3 Shift Passdown · S5 · 2026-05-17 · MST: Stanley A &nbsp;·&nbsp;
     Shift ID #42 &nbsp;·&nbsp; {len(FAKE_ENTRIES)} raw DB entries &nbsp;→&nbsp;
     {len(result.consolidated)} consolidated WO blocks + {len(result.passthrough)} passthrough</p>
</div>

<div class="container">

  <!-- Stats -->
  <div class="stat-bar">
    <div class="stat">
      <div class="num" style="color:{RED};">{len(FAKE_ENTRIES)}</div>
      <div class="lbl">Raw DB entries (what techs uploaded)</div>
    </div>
    <div class="stat">
      <div class="num" style="color:{RED};">1</div>
      <div class="lbl">Excluded by MST (never in report)</div>
    </div>
    <div class="stat">
      <div class="num" style="color:{BLUE};">{len(result.consolidated)}</div>
      <div class="lbl">Unique WOs consolidated</div>
    </div>
    <div class="stat">
      <div class="num" style="color:#64748b;">{len(result.passthrough)}</div>
      <div class="lbl">Passthrough (no WO number)</div>
    </div>
    <div class="stat">
      <div class="num" style="color:{GREEN};">{len(happenings)}</div>
      <div class="lbl">Final report happenings</div>
    </div>
  </div>

  <!-- Side by side -->
  <div class="cols">
    <div class="before">
      <div class="col-head">❌ BEFORE — What the report used to show</div>
      <p style="font-size:13px;color:#64748b;margin-bottom:14px;">
        The old <code>seen_wo</code> set picked whichever entry arrived first and
        silently dropped everything else. Isaac's first upload won; Stanley's and
        Marcus's remarks were gone forever from the report.
      </p>
      {before_html}
    </div>

    <div style="display:flex;align-items:flex-start;">
      <div class="arrow">→</div>
    </div>

    <div class="after">
      <div class="col-head">✅ AFTER — One consolidated block per WO</div>
      <p style="font-size:13px;color:#64748b;margin-bottom:14px;">
        All techs' remarks are merged chronologically, deduplicated, and
        combined into a single authoritative block. The excluded entry never
        appears. Raw DB rows are untouched.
      </p>
      {after_consolidated}
      <div class="section-label">📌 Passthrough entries (no WO number — unchanged)</div>
      {after_passthrough}
    </div>
  </div>

  <div style="margin-top:32px;background:white;border-radius:12px;padding:20px 24px;
              border:1px solid #e2e8f0;">
    <div style="font-weight:800;font-size:15px;margin-bottom:12px;color:{BLUE};">
      📄 Final Passdown Report — Happenings Section
    </div>
    <p style="font-size:13px;color:#64748b;margin-bottom:16px;">
      This is exactly what ends up in the email / PDF passdown:
    </p>
    {"".join(
      f'<div style="display:flex;gap:10px;padding:10px 0;border-bottom:1px solid #f1f5f9;">'
      f'<span style="color:{BLUE};font-weight:700;font-size:18px;flex-shrink:0;">•</span>'
      f'<div>'
      f'<div style="font-weight:700;font-size:14px;">{h["title"]}'
      + (f' <span style="font-size:12px;color:#64748b;font-weight:400;">(WO #{h["wo_number"]})</span>' if h["wo_number"] else "")
      + (f' <span style="font-size:12px;color:#64748b;font-weight:400;">— by {h["tech_name"]}</span>' if h["tech_name"] else "")
      + f'</div>'
      f'<div style="font-size:13px;color:#334155;line-height:1.7;margin-top:3px;">{h["paragraph"]}</div>'
      f'</div></div>'
      for h in happenings
    )}
  </div>

</div>
</body>
</html>"""

out_path = os.path.join(os.path.dirname(__file__), "_demo_consolidator.html")
with open(out_path, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Demo written -> {out_path}")
webbrowser.open(f"file:///{out_path.replace(os.sep, '/')}")

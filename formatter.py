"""Report formatter — HTML preview and email/plain-text output.

Public API
----------
format_html_preview(data)  -> str   (inner HTML for the document card)
format_email(data)         -> str   (plain-text email version)
"""
from __future__ import annotations
from datetime import datetime
from report_models import ReportData


# ── Helpers ──────────────────────────────────────────────────────────────────────────────

def _fmt_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        return iso


# ── HTML preview ─────────────────────────────────────────────────────────────────────

def _none(msg: str = "No activity recorded this shift.") -> str:
    return f'<p style="color:#444;font-style:italic;font-size:15px;">{msg}</p>'


def _sec(icon: str, title: str, body: str) -> str:
    return (
        '<div style="margin-bottom:2rem;">'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
        f'<span style="font-size:1rem;">{icon}</span>'
        f'<h3 style="font-size:13px;font-weight:900;text-transform:uppercase;'
        f'letter-spacing:0.08em;color:#000;margin:0;">{title}</h3></div>'
        f'<div style="padding-left:4px;font-size:15px;line-height:1.75;color:#000;">{body}</div>'
        '</div>'
    )


def format_html_preview(data: ReportData) -> str:
    """Return inner body HTML for the document preview card."""
    out: list[str] = []

    # 1. Vendors
    if data.vendors:
        rows = "".join(
            f'<li style="display:flex;gap:8px;margin-bottom:4px;">'
            f'<span style="color:#000;flex-shrink:0;">&#9658;</span>{v}</li>'
            for v in data.vendors
        )
        body = f'<ul style="list-style:none;margin:0;padding:0;">{rows}</ul>'
    else:
        body = _none("No vendor activity recorded during this shift.")
    out.append(_sec("&#128666;", "Vendors", body))

    # 2. Shift Happenings — PM count first (auto), then asset entries
    happening_lines: list[str] = []

    if data.pm_count:
        label = f"{data.pm_count} PM{'s' if data.pm_count != 1 else ''} completed this shift"
        happening_lines.append(
            f'<li style="padding:8px 0;border-bottom:1px solid #eee;font-size:14px;color:#111;line-height:1.7;">'
            f'<strong style="color:#000;">PM:</strong> {label}</li>'
        )

    for h in data.happenings:
        wo_ref = f" <span style='color:#555;font-size:13px;'>(WO #{h.wo_number})</span>" if h.wo_number else ""
        happening_lines.append(
            f'<li style="padding:8px 0;border-bottom:1px solid #eee;font-size:14px;color:#111;line-height:1.7;">'
            f'<strong style="color:#000;">{h.title}:</strong> {h.paragraph}.{wo_ref}</li>'
        )

    if happening_lines:
        body = f'<ul style="list-style:none;margin:0;padding:0;">{"".join(happening_lines)}</ul>'
    else:
        body = _none("No shift happenings recorded.")
    out.append(_sec("&#9889;", "Shift Happenings", body))

    # 3. Reliable Operations
    if data.ro:
        rows = "".join(
            f'<li style="display:flex;gap:8px;margin-bottom:4px;">'
            f'<span style="color:#000;flex-shrink:0;">&#9658;</span>{r}</li>'
            for r in data.ro
        )
        body = f'<ul style="list-style:none;margin:0;padding:0;">{rows}</ul>'
    else:
        body = _none("No reliable operations recorded this shift.")
    out.append(_sec("&#128203;", "Reliable Operations", body))

    # 4. RTS
    if data.rts:
        rows = "".join(
            f'<li style="display:flex;gap:8px;margin-bottom:4px;">'
            f'<span style="font-weight:700;color:#000;flex-shrink:0;">&#10004;</span>'
            f'<span><strong>{u}</strong> &mdash; Returned to Service</span></li>'
            for u in data.rts
        )
        body = f'<ul style="list-style:none;margin:0;padding:0;">{rows}</ul>'
    else:
        body = _none("No units returned to service this shift.")
    out.append(_sec("&#9989;", "Out of Service &ndash; Returned to Service (RTS)", body))

    # 5. Current OOS
    if data.oos:
        rows = "".join(
            f'<li style="display:flex;gap:8px;margin-bottom:4px;">'
            f'<span style="font-weight:700;color:#000;flex-shrink:0;">&#9940;</span>'
            f'<strong>{o.unit}</strong></li>'
            for o in data.oos
        )
        body = f'<ul style="list-style:none;margin:0;padding:0;">{rows}</ul>'
    else:
        body = _none("No equipment currently out of service.")
    out.append(_sec("&#9940;", "Current Out of Service Equipment", body))

    return (
        '<div style="font-family:\'Times New Roman\',Times,serif">'
        + "\n".join(out)
        + '</div>'
    )


# ── Passdown email (clean bullet format) ────────────────────────────────────────

def format_email_passdown(data: ReportData) -> str:
    """Clean bullet-point passdown email matching the standard IND3 format.

    Example output:
        D2 End of Shift Passdown

        Vendors
        • None

        Shift Happenings
        • Worked on scheduled PMs.
        • BALER0005: ABM requested help securing the bale...

        PM's Needing to be Completed
        • None

        Out of Service — Returned to Service (RTS)
        • None

        Current Out of Service
        • M2785
        • WAV0015

        All LOTO Keys verified
    """
    def _bullets(items: list[str]) -> str:
        return "\n".join(f"\u2022 {i}" for i in items) if items else "\u2022 None"

    # ── Header ──
    lines: list[str] = [
        f"{data.shift_type} End of Shift Passdown",
        "",
    ]

    # ── Vendors ──
    lines += ["Vendors", _bullets(data.vendors), ""]

    # ── Shift Happenings ──
    h_items: list[str] = []
    if data.pm_count:
        label = f"{data.pm_count} PM{'s' if data.pm_count != 1 else ''} completed this shift"
        h_items.append(label)
    for h in data.happenings:
        # Use unit prefix if title looks like a unit code, else just the paragraph
        prefix = h.title.split("\u2013")[0].split("-")[0].strip()
        if prefix and not any(c in prefix for c in (" ", ",")):
            h_items.append(f"{prefix}: {h.paragraph}")
        else:
            h_items.append(h.paragraph)
    lines += ["Shift Happenings", _bullets(h_items), ""]

    # ── PM's Needing to be Completed (Reliable Operations) ──
    lines += ["PM's Needing to be Completed", _bullets(list(data.ro)), ""]

    # ── Out of Service — RTS ──
    lines += ["Out of Service \u2014 Returned to Service (RTS)", _bullets(list(data.rts)), ""]

    # ── Current Out of Service ──
    oos_names = [o.unit for o in data.oos]
    lines += ["Current Out of Service", _bullets(oos_names), ""]

    # ── Footer ──
    lines.append("All LOTO Keys verified")

    return "\n".join(lines)


def format_email_passdown_html(
    data: ReportData,
    loto_missing: str = "",
    pending_pms: list[dict] | None = None,
    lead_title: str = "MST Lead",
) -> str:
    """HTML passdown email: bold titles, real bordered PM table, signature block."""
    from datetime import date as _date

    def _li(items: list[str]) -> str:
        if not items:
            return "<li style='margin:2px 0;color:#555;'>None</li>"
        return "".join(f"<li style='margin:4px 0;'>{i}</li>" for i in items)

    def _bullet_sec(title: str, items: list[str]) -> str:
        return (
            f"<p style='margin:16px 0 4px;'><strong style='font-size:14px;'>{title}</strong></p>"
            f"<ul style='margin:0 0 4px;padding-left:20px;line-height:1.8;'>{_li(items)}</ul>"
        )

    # Shift Happenings
    h_items: list[str] = []
    if data.pm_count:
        h_items.append(f"{data.pm_count} PM{'s' if data.pm_count != 1 else ''} completed this shift")
    for h in data.happenings:
        prefix = h.title.split("\u2013")[0].split("-")[0].strip()
        wo_tag = (
            f" <span style='color:#6b7280;font-size:12px;'>(WO# {h.wo_number})</span>"
            if getattr(h, "wo_number", "") else ""
        )
        if prefix and " " not in prefix and "," not in prefix:
            h_items.append(f"<strong>{prefix}:</strong> {h.paragraph}{wo_tag}")
        else:
            h_items.append(f"{h.paragraph}{wo_tag}")

    # PM section: proper bordered table or bullet fallback
    today  = _date.today().strftime("%m/%d/%Y")
    border = "border:1px solid #d1d5db;"
    th_s   = f"padding:8px 12px;text-align:left;font-size:12px;font-weight:bold;background:#1a56db;color:#fff;{border}"
    td_s   = f"padding:7px 12px;font-size:12px;{border}"

    if pending_pms:
        hdr = (
            f"<tr><th style='{th_s}'>WO #</th><th style='{th_s}'>Asset</th>"
            f"<th style='{th_s}'>PM Due Date</th><th style='{th_s}'>Promise Date / Time</th>"
            f"<th style='{th_s}'>WO Date / Time</th></tr>"
        )
        body_rows = ""
        for i, r in enumerate(pending_pms):
            due     = r.get("pm_due_date", "")
            hot     = today in due
            row_bg  = "#fef08a" if hot else ("#f9fafb" if i % 2 == 0 else "#ffffff")
            due_col = "color:#c00;" if hot else ""
            body_rows += (
                f"<tr style='background:{row_bg};'>"
                f"<td style='{td_s}font-family:monospace;'>{r.get('wo_number','')}</td>"
                f"<td style='{td_s}font-weight:bold;'>{r.get('asset_num','')}</td>"
                f"<td style='{td_s}font-weight:bold;{due_col}'>{due}</td>"
                f"<td style='{td_s}'>{r.get('promise_date','')}</td>"
                f"<td style='{td_s}color:#666;'>{r.get('wo_date','')}</td>"
                f"</tr>"
            )
        pm_html = (
            f"<p style='margin:16px 0 4px;'><strong style='font-size:14px;'>PM\u2019s Needing to be Completed</strong></p>"
            f"<table style='width:100%;border-collapse:collapse;margin-top:4px;'>"
            f"<thead>{hdr}</thead><tbody>{body_rows}</tbody></table>"
        )
    else:
        pm_html = _bullet_sec("PM\u2019s Needing to be Completed", list(data.ro) if data.ro else [])

    # LOTO
    loto_html = "<p style='margin-top:18px;'><strong>All LOTO Keys Verified &#10003;</strong></p>"
    if loto_missing:
        loto_html += f"<p style='color:#c00;margin:2px 0;'><strong>Missing keys:</strong> {loto_missing}</p>"

    # Signature — Outlook appends MST facility block automatically
    sig = (
        "<div style='margin-top:28px;padding-top:14px;border-top:2px solid #e5e7eb;'>"
        f"<p style='margin:0;font-weight:bold;font-size:14px;'>{data.lead_name}</p>"
        f"<p style='margin:2px 0;font-size:13px;color:#444;'>{lead_title}</p>"
        "</div>"
    )

    return (
        "<html><body style='font-family:Calibri,Arial,sans-serif;font-size:14px;"
        "color:#111;max-width:700px;line-height:1.6;'>"
        f"<p style='margin:0 0 18px;font-size:16px;'><strong>{data.shift_type} End of Shift Passdown</strong></p>"
        + _bullet_sec("Vendors", list(data.vendors))
        + _bullet_sec("Shift Happenings", h_items)
        + pm_html
        + _bullet_sec("Out of Service \u2014 Returned to Service (RTS)", list(data.rts))
        + _bullet_sec("Current Out of Service", [o.unit for o in data.oos])
        + loto_html
        + sig
        + "</body></html>"
    )


W    = 64
_DIV  = "=" * W
_RULE = "-" * W


def _txt_sec(title: str, lines: list[str]) -> str:
    body = lines if lines else ["  None this shift."]
    return "\n".join(["", title.upper(), _RULE] + body)


def format_email(data: ReportData) -> str:
    """Return a structured plain-text report suitable for email or .txt download."""
    dc       = f"  |  {data.dc_number}" if data.dc_number else ""
    date_str = _fmt_date(data.date)

    header = "\n".join([
        _DIV,
        f"  WALMART RELIABILITY OPERATIONS{dc}",
        f"  SHIFT PASSDOWN REPORT",
        _DIV,
        "",
        f"  Date:         {date_str}",
        f"  Shift:        {data.shift_type}",
        f"  Prepared By:  {data.lead_name}",
        "",
    ])

    secs: list[str] = []

    secs.append(_txt_sec("Vendors",
        [f"  \u25b6  {v}" for v in data.vendors]
        or ["  No vendor activity recorded during this shift."]))

    h_lines: list[str] = []
    if data.pm_count:
        h_lines.append(f"  •  {data.pm_count} PM{'s' if data.pm_count != 1 else ''} completed")
    for h in data.happenings:
        wo = f" (WO #{h.wo_number})." if h.wo_number else "."
        h_lines.append(f"  \u2022  {h.title}: {h.paragraph}.{wo}")
    secs.append(_txt_sec("Shift Happenings",
        h_lines or ["  No shift happenings recorded."]))

    secs.append(_txt_sec("Reliable Operations",
        [f"  \u25b6  {r}" for r in data.ro]
        or ["  No reliable operations recorded this shift."]))

    secs.append(_txt_sec("Returned to Service (RTS)",
        [f"  \u2713  {u}  —  Returned to Service" for u in data.rts]
        or ["  No units returned to service this shift."]))

    secs.append(_txt_sec("Current Out of Service Equipment",
        [f"  [OOS]  {o.unit}" for o in data.oos]
        or ["  No equipment currently out of service."]))

    footer = "\n".join([
        "",
        _DIV,
        f"  End of Report  |  {data.shift_type}  |  {date_str}  |  MST: {data.lead_name}",
        _DIV,
        "",
    ])

    return header + "\n".join(secs) + "\n" + footer

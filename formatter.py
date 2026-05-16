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


# ── Plain-text / TXT download ─────────────────────────────────────────────────

W  = 64                        # report column width
_DIV = "=" * W
_RULE = "─" * W


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

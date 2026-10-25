"""Report formatter — HTML preview and email/plain-text output.

Public API
----------
format_html_preview(data)  -> str   (inner HTML for the document card)
format_email(data)         -> str   (plain-text email version)
"""
from __future__ import annotations
from datetime import datetime
from html import escape
from v3_report_layout import heading as _heading, remark as _remark
from report_models import ReportData
from pm_table_style import pm_zebra_hex, PM_HEADER_HEX


# ── Helpers ──────────────────────────────────────────────────────────────────────────────

def _fmt_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%B %d, %Y")
    except Exception:
        return iso


# ── HTML preview ─────────────────────────────────────────────────────────────────────

def _none(msg: str = "No activity recorded this shift.") -> str:
    return f'<p style="color:#444;font-style:italic;font-size:14px;">{msg}</p>'


def _sec(icon: str, title: str, body: str) -> str:
    return (
        '<div style="margin-bottom:2rem;">'
        f'<div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">'
        f'<span style="font-size:1rem;">{icon}</span>'
        f'<h3 style="font-size:16px;font-weight:900;text-transform:uppercase;'
        f'letter-spacing:0.08em;color:#000;margin:0;">{title}</h3></div>'
        f'<div style="padding-left:4px;font-size:14px;line-height:1.75;color:#000;">{body}</div>'
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

    # 2. Shift Happenings — MST notes first (general shift context), then
    # PM count (auto), then asset entries
    happening_lines: list[str] = []

    if data.shift_notes and data.shift_notes.strip():
        happening_lines.append(
            f'<li style="padding:8px 0;border-bottom:1px solid #eee;font-size:14px;color:#111;line-height:1.7;white-space:pre-wrap;">'
            f'{data.shift_notes.strip()}</li>'
        )

    if data.pm_count:
        label = f"{data.pm_count} PM{'s' if data.pm_count != 1 else ''} completed this shift"
        happening_lines.append(
            f'<li style="padding:8px 0;border-bottom:1px solid #eee;font-size:14px;color:#111;line-height:1.7;">'
            f'<strong style="color:#000;">PM:</strong> {label}</li>'
        )

    for h in data.happenings:
        happening_lines.append(
            '<li style="padding:8px 0;border-bottom:1px solid #eee;'
            'font-size:14px;line-height:1.7;">'
            f'<strong>{escape(_heading(h))}</strong>'
            '<ul style="margin:4px 0 0 6px;padding-left:20px;list-style:disc;">'
            f'<li style="font-size:13px;white-space:pre-wrap;">{escape(_remark(h))}</li>'
            '</ul></li>'
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

    # ── Shift Happenings ── (MST notes lead, then PM count, then asset entries)
    h_lines: list[str] = []
    if data.shift_notes and data.shift_notes.strip():
        h_lines.append("\u2022 " + data.shift_notes.strip())
    if data.pm_count:
        h_lines.append(f"\u2022 {data.pm_count} PM{'s' if data.pm_count != 1 else ''} completed this shift")
    for h in data.happenings:
        h_lines.append(_heading(h))
        h_lines.append(f"  \u2022 {_remark(h)}")
    lines += ["Shift Happenings", "\n".join(h_lines) if h_lines else "\u2022 None", ""]

    # ── Reliable Operations ──
    lines += ["Reliable Operations", _bullets(list(data.ro)), ""]

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
    loto_verified: bool = False,
) -> str:
    """HTML passdown email: bold titles, real bordered PM table, signature block."""

    def _li(items: list[str]) -> str:
        """Outlook needs explicit font-size on every element -- body inheritance is ignored."""
        s = "font-size:14px;font-family:Calibri,Arial,sans-serif;margin:4px 0;"
        if not items:
            return f"<li style='{s}color:#555;'>None</li>"
        return "".join(f"<li style='{s}'>{i}</li>" for i in items)

    def _bullet_sec(title: str, items: list[str]) -> str:
        return (
            f"<p style='margin:16px 0 4px;font-family:Calibri,Arial,sans-serif;'>"
            f"<strong style='font-size:16px;'>{title}</strong></p>"
            f"<ul style='margin:0 0 4px;padding-left:20px;line-height:1.8;"
            f"font-size:14px;font-family:Calibri,Arial,sans-serif;'>{_li(items)}</ul>"
        )

    # Shift Happenings (MST notes lead, then PM count, then asset entries)
    h_blocks: list[str] = []
    if data.shift_notes and data.shift_notes.strip():
        h_blocks.append(f'<p style="margin:6px 0;font-size:14px;">{escape(data.shift_notes.strip())}</p>')
    if data.pm_count:
        h_blocks.append(
            f"<p style='margin:6px 0;font-size:14px;'>{data.pm_count} PM"
            f"{'s' if data.pm_count != 1 else ''} completed this shift</p>"
        )
    for h in data.happenings:
        h_blocks.append(
            f'<div style="margin:8px 0 10px;">'
            f'<strong style="font-size:14px;">{escape(_heading(h))}</strong>'
            f'<ul style="margin:2px 0 0;padding-left:22px;">'
            f'<li style="font-size:13px;white-space:pre-wrap;">{escape(_remark(h))}</li>'
            '</ul></div>'
        )
    shift_happenings_html = (
        "<p style='margin:16px 0 4px;font-family:Calibri,Arial,sans-serif;'>"
        "<strong style='font-size:16px;'>Shift Happenings</strong></p>"
        + "".join(h_blocks or ["<p>None</p>"])
    )

    # PM section: proper bordered table or bullet fallback
    # No due-date alarm color -- these are routine daily PMs, "due today"
    # is the normal state, not an exception. See pm_table_style.py.
    border = "border:1px solid #d1d5db;"
    th_s   = f"padding:8px 12px;text-align:left;font-size:14px;font-weight:bold;background:#{PM_HEADER_HEX};color:#fff;font-family:Calibri,Arial,sans-serif;{border}"
    td_s   = f"padding:7px 12px;font-size:14px;font-family:Calibri,Arial,sans-serif;{border}"

    if pending_pms:
        hdr = (
            f"<tr><th style='{th_s}'>WO #</th><th style='{th_s}'>Asset</th>"
            f"<th style='{th_s}'>PM Due Date</th><th style='{th_s}'>Promise Date / Time</th>"
            f"<th style='{th_s}'>WO Date / Time</th></tr>"
        )
        body_rows = ""
        for i, r in enumerate(pending_pms):
            due    = r.get("pm_due_date", "")
            row_bg = f"#{pm_zebra_hex(i)}"
            body_rows += (
                f"<tr style='background:{row_bg};'>"
                f"<td style='{td_s}font-family:monospace;'>{r.get('wo_number','')}</td>"
                f"<td style='{td_s}font-weight:bold;'>{r.get('asset_num','')}</td>"
                f"<td style='{td_s}font-weight:bold;'>{due}</td>"
                f"<td style='{td_s}'>{r.get('promise_date','')}</td>"
                f"<td style='{td_s}color:#666;'>{r.get('wo_date','')}</td>"
                f"</tr>"
            )
        pm_count_label = f"PM\u2019s Needing to be Completed ({len(pending_pms)})"
        pm_html = (
            f"<p style='margin:16px 0 2px;'><strong style='font-size:16px;'>PM\u2019s Needing to be Completed</strong></p>"
            f"<p style='margin:0 0 6px;font-size:14px;font-weight:bold;color:#dc2626;'>{len(pending_pms)} PM{'s' if len(pending_pms) != 1 else ''} pending this shift</p>"
            f"<table style='width:100%;border-collapse:collapse;margin-top:4px;'>"
            f"<thead>{hdr}</thead><tbody>{body_rows}</tbody></table>"
        )
    else:
        pm_html = (
            f"<p style='margin:16px 0 2px;'><strong style='font-size:16px;'>PM\u2019s Needing to be Completed</strong></p>"
            f"<p style='margin:0 0 6px;font-size:14px;color:#6b7280;font-style:italic;'>No PMs pending this shift.</p>"
        )

    # LOTO -- only show verified if actually verified
    _loto_s = "font-size:14px;font-family:Calibri,Arial,sans-serif;margin-top:18px;"
    if loto_verified:
        loto_html = f"<p style='{_loto_s}'><strong style='color:#2a8703;'>&#10003; All LOTO Keys Verified</strong></p>"
    else:
        loto_html = f"<p style='{_loto_s}'><strong style='color:#b45309;'>&#9888; LOTO Verification Pending</strong></p>"
    if loto_missing:
        loto_html += f"<p style='color:#c00;margin:2px 0;font-size:14px;font-family:Calibri,Arial,sans-serif;'><strong>Missing keys:</strong> {loto_missing}</p>"

    # No sign-off block — Outlook appends the user's own signature automatically
    # below the compose body. Putting a name here would create a duplicate.

    return (
        "<html><body style='font-family:Calibri,Arial,sans-serif;font-size:14px;"
        "color:#111;max-width:700px;line-height:1.6;'>"
        f"<p style='margin:0 0 18px;font-size:18px;'><strong>{data.shift_type} End of Shift Passdown</strong></p>"
        + _bullet_sec("Vendors", list(data.vendors))
        + shift_happenings_html
        + _bullet_sec("Reliable Operations", list(data.ro))
        + pm_html
        + _bullet_sec("Out of Service \u2014 Returned to Service (RTS)", list(data.rts))
        + _bullet_sec("Current Out of Service", [o.unit for o in data.oos])
        + loto_html
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
    if data.shift_notes and data.shift_notes.strip():
        h_lines.append(f"  \u2022  {data.shift_notes.strip()}")
    if data.pm_count:
        h_lines.append(f"  \u2022  {data.pm_count} PM{'s' if data.pm_count != 1 else ''} completed")
    for h in data.happenings:
        h_lines.append(f"  {_heading(h)}")
        h_lines.append(f"    \u2022  {_remark(h)}")
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

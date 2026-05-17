"""Patch formatter.py: replace format_email_passdown_html with full rewrite."""
import pathlib, re

path = pathlib.Path("formatter.py")
src  = path.read_text(encoding="utf-8")

OLD_START = "def format_email_passdown_html(data: ReportData, loto_missing: str = \"\", pending_pms: list[dict] | None = None) -> str:"
OLD_END   = "    )\n\n\nW    = 64"

start_idx = src.index(OLD_START)
end_idx   = src.index(OLD_END, start_idx) + len("    )\n\n\n")

NEW_FN = '''def format_email_passdown_html(
    data: "ReportData",
    loto_missing: str = "",
    pending_pms: "list[dict] | None" = None,
    lead_title: str = "MST Lead",
) -> str:
    """HTML passdown email: bold titles, real bordered PM table, signature."""
    from datetime import date as _date

    # helpers
    def _li(items):
        if not items:
            return "<li style='margin:2px 0;color:#555;'>None</li>"
        return "".join(f"<li style='margin:4px 0;'>{i}</li>" for i in items)

    def _bullet_section(title, items):
        return (
            f"<p style='margin:16px 0 4px;'><strong style='font-size:14px;'>{title}</strong></p>"
            f"<ul style='margin:0 0 4px;padding-left:20px;line-height:1.8;'>{_li(items)}</ul>"
        )

    # Shift Happenings
    h_items = []
    if data.pm_count:
        h_items.append(f"{data.pm_count} PM{'s' if data.pm_count != 1 else ''} completed this shift")
    for h in data.happenings:
        prefix = h.title.split("\\u2013")[0].split("-")[0].strip()
        if prefix and " " not in prefix and "," not in prefix:
            h_items.append(f"<strong>{prefix}:</strong> {h.paragraph}")
        else:
            h_items.append(h.paragraph)

    # PM section: real table if parsed data exists, bullet fallback otherwise
    today = _date.today().strftime("%m/%d/%Y")
    border = "border:1px solid #d1d5db;"
    th_s = (
        f"padding:8px 12px;text-align:left;font-size:12px;font-weight:bold;"
        f"background:#1a56db;color:#ffffff;{border}"
    )
    td_s = f"padding:7px 12px;font-size:12px;{border}"

    if pending_pms:
        header = (
            f"<tr>"
            f"<th style=\\'{th_s}\\'>WO #</th>"
            f"<th style=\\'{th_s}\\'>Asset</th>"
            f"<th style=\\'{th_s}\\'>PM Due Date</th>"
            f"<th style=\\'{th_s}\\'>Promise Date / Time</th>"
            f"<th style=\\'{th_s}\\'>WO Date / Time</th>"
            f"</tr>"
        )
        body_rows = ""
        for i, r in enumerate(pending_pms):
            due      = r.get("pm_due_date", "")
            is_today = today in due
            row_bg   = "#fef08a" if is_today else ("#f9fafb" if i % 2 == 0 else "#ffffff")
            due_col  = "#c00" if is_today else "#111"
            body_rows += (
                f"<tr style='background:{row_bg};'>"
                f"<td style=\\'{td_s};font-family:monospace;\\'>{r.get('wo_number','')}</td>"
                f"<td style=\\'{td_s};font-weight:bold;\\'>{r.get('asset_num','')}</td>"
                f"<td style=\\'{td_s};font-weight:bold;color:{due_col};\\'>{due}</td>"
                f"<td style=\\'{td_s}\\'>{r.get('promise_date','')}</td>"
                f"<td style=\\'{td_s};color:#666;\\'>{r.get('wo_date','')}</td>"
                f"</tr>"
            )
        pm_section = (
            f"<p style='margin:16px 0 4px;'><strong style='font-size:14px;'>PM\\u2019s Needing to be Completed</strong></p>"
            f"<table style='width:100%;border-collapse:collapse;margin-top:4px;'>"
            f"<thead>{header}</thead><tbody>{body_rows}</tbody></table>"
        )
    else:
        pm_section = _bullet_section("PM\\u2019s Needing to be Completed", list(data.ro) if data.ro else [])

    # LOTO
    loto_html = "<p style='margin-top:18px;'><strong>All LOTO Keys Verified &#10003;</strong></p>"
    if loto_missing:
        loto_html += f"<p style='color:#c00;margin:2px 0;'><strong>Missing keys:</strong> {loto_missing}</p>"

    # Signature
    sig_html = (
        "<div style='margin-top:32px;padding-top:16px;border-top:2px solid #e5e7eb;"
        "font-family:Calibri,Arial,sans-serif;'>"
        f"<p style='margin:0;font-weight:bold;font-size:14px;'>{data.lead_name}</p>"
        f"<p style='margin:2px 0;font-size:13px;color:#444;'>{lead_title}</p>"
        "<p style='margin:10px 0 2px;font-size:12px;color:#555;'>eCommerce Fulfillment Center # 7552/8972</p>"
        "<p style='margin:0;font-size:12px;color:#555;'>5259 W. 500 North</p>"
        "<p style='margin:0;font-size:12px;color:#555;'>McCordsville, Indiana, 46055</p>"
        "<p style='margin:8px 0 0;font-size:12px;'>"
        "<strong style='color:#0053e2;'>Walmart</strong> "
        "<span style='color:#ffc220;'>&#10043;</span> "
        "<em>Save money. Live better.</em></p>"
        "</div>"
    )

    return (
        "<html><body style='font-family:Calibri,Arial,sans-serif;font-size:14px;"
        "color:#111;max-width:700px;line-height:1.6;'>"
        f"<p style='margin:0 0 18px;font-size:16px;'>"
        f"<strong>{data.shift_type} End of Shift Passdown</strong></p>"
        + _bullet_section("Vendors", list(data.vendors))
        + _bullet_section("Shift Happenings", h_items)
        + pm_section
        + _bullet_section("Out of Service \\u2014 Returned to Service (RTS)", list(data.rts))
        + _bullet_section("Current Out of Service", [o.unit for o in data.oos])
        + loto_html
        + sig_html
        + "</body></html>"
    )


'''

new_src = src[:start_idx] + NEW_FN + src[end_idx:]
path.write_text(new_src, encoding="utf-8")
print(f"Patched. New length: {len(new_src)} chars")
print("Verifying import...")
import subprocess, sys
r = subprocess.run([sys.executable, "-c", "import formatter; print('OK')"], capture_output=True, text=True)
print(r.stdout, r.stderr)

"""Compact Walmart Next Gen signature for downloaded Outlook passdown drafts.

Only adds a signature after the existing report. No report formatting or
recipient/subject/EML serialization changes are made here.
"""
from __future__ import annotations

import html
import re

SIGNATURE_MARKER = "<!-- IND3-NEXT-GEN-SIGNATURE -->"
ADDRESS = "5259 W. 500 N., McCordsville, IN 46055"

# Exact-name matches avoid collisions and prevent guessed email addresses.
KNOWN = {
    "isaac ssaku": ("Maintenance Technician", "isaac.ssaku@walmart.com"),
    "stanley augustin": ("Maintenance Technician", "stanley.augustin@walmart.com"),
    "alexander carr": ("Maintenance Technician", "Alexander.Carr@walmart.com"),
    "len ward": ("Maintenance Technician", "len.ward@walmart.com"),
    "kevin smith": ("Maintenance Technician", "Kevin.Smith1@walmart.com"),
}


def get_signature_html(
    lead_name: str, shift: str = "", role: str = "", email: str = ""
) -> str:
    """Use the report's shift owner, with only verified contact overrides.

    Unknown employees retain their own name. Their title and email are not
    fabricated; callers can supply verified role/email as the app evolves.
    """
    name = (lead_name or "").strip() or "IND3 Maintenance Team"
    known_role, known_email = KNOWN.get(name.casefold(), ("", ""))
    job_title = (role or known_role or "IND3 Team Member").strip()
    mail = (email or known_email).strip()
    shift_text = (shift or "").strip()
    name_html = html.escape(name)
    title_html = html.escape(job_title + (f" | {shift_text}" if shift_text else ""))
    email_html = ""
    if mail:
        safe_email = html.escape(mail, quote=True)
        email_html = (
            "<tr><td style='padding:2px 0;color:#334155;font-size:12px;'>"
            f"<a href='mailto:{safe_email}' style='color:#0053e2;"
            f"text-decoration:none;'>{safe_email}</a></td></tr>"
        )
    return (
        SIGNATURE_MARKER
        + "<table role='presentation' cellpadding='0' cellspacing='0' border='0' "
        "style='font-family:Calibri,Arial,sans-serif;margin-top:18px;"
        "border-top:1px solid #dce3ed;width:100%;max-width:520px;'>"
        "<tr><td style='padding-top:12px;vertical-align:top;width:4px;"
        "background:#0053e2;'>&nbsp;</td>"
        "<td style='padding:12px 0 0 13px;'>"
        "<table role='presentation' cellpadding='0' cellspacing='0' border='0'>"
        f"<tr><td style='font-size:17px;font-weight:700;color:#172b4d;"
        f"padding:0 0 2px;'>{name_html}</td></tr>"
        f"<tr><td style='font-size:13px;font-weight:700;color:#0053e2;"
        f"padding:0 0 2px;'>{title_html}</td></tr>"
        "<tr><td style='font-size:12px;color:#64748b;padding:0 0 8px;'>"
        "IND3 &middot; Next Generation Fulfillment Center #8972</td></tr>"
        + email_html +
        "<tr><td style='font-size:12px;color:#475569;padding:2px 0 8px;'>"
        + html.escape(ADDRESS) + "</td></tr>"
        "<tr><td style='border-top:1px solid #e2e8f0;padding-top:8px;'>"
        "<span style='font-size:22px;color:#ffc220;vertical-align:middle;'>"
        "&#10035;</span>&nbsp;"
        "<span style='font-size:17px;font-weight:800;color:#0053e2;'>"
        "Walmart</span>&nbsp;&nbsp;"
        "<span style='font-size:10px;font-weight:700;letter-spacing:.5px;"
        "color:#64748b;'>NEXT GEN FULFILLMENT</span>"
        "<span style='font-size:11px;color:#64748b;'>"
        "&nbsp;&nbsp;|&nbsp;&nbsp;Maintenance Operations</span>"
        "</td></tr></table></td></tr></table>"
    )


def append_signature(
    body_html: str, lead_name: str, shift: str = "", role: str = "", email: str = ""
) -> str:
    """Append exactly once before the closing body tag, if present."""
    if SIGNATURE_MARKER in body_html:
        return body_html
    signature = get_signature_html(lead_name, shift, role, email)
    if re.search(r"</body\s*>", body_html, flags=re.IGNORECASE):
        return re.sub(
            r"</body\s*>", lambda match: signature + match.group(0),
            body_html, count=1, flags=re.IGNORECASE
        )
    return body_html + signature

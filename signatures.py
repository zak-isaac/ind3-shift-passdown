"""
MST email signatures — embedded into .eml so leads don't need
Outlook configured. Matched by lead_name (case-insensitive, partial).
"""
import os as _os

# ── Walmart logo (base64 PNG, rendered from SVG via svglib) ────────────────────
# Embedded as data URI so the logo shows in Outlook without external requests.
_LOGO_B64_PATH = _os.path.join(_os.path.dirname(__file__), "static", "walmart-logo.b64")
try:
    with open(_LOGO_B64_PATH) as _f:
        _LOGO_B64 = _f.read().strip()
except FileNotFoundError:
    _LOGO_B64 = ""  # graceful fallback — logo just won't appear

_WM_LOGO = (
    f"<img src='data:image/png;base64,{_LOGO_B64}' "
    "alt='Walmart Supply Chain' "
    "width='150' style='display:block;margin-top:6px;' />"
) if _LOGO_B64 else (
    "<span style='font-size:18px;font-weight:900;color:#0053e2;"
    "font-family:Calibri,Arial,sans-serif;'>Walmart Supply Chain</span>"
)

# ── Shared address block ────────────────────────────────────────────────────────
_ADDR = "5259 W. 500 N.<br>McCordsville, IN&nbsp; 46055"
_WM_LOGO = (
    "<span style='font-size:20px;font-weight:900;"
    "font-family:Calibri,Arial,sans-serif;'>"
    "<span style='color:#0053e2;'>Walmart Supply Chain</span>"
    "</span>"
)

# ── Individual signatures ───────────────────────────────────────────────────────
_SIGS: dict[str, str] = {

    "stanley": (
        "<div style='font-family:Calibri,Arial,sans-serif;margin-top:24px;"
        "padding-top:14px;border-top:1px solid #d1d5db;line-height:1.5;'>"
        "<p style='margin:0;font-size:15px;font-weight:700;color:#4da6ff;'>"
        "Stanley Augustin</p>"
        "<p style='margin:2px 0;font-size:14px;font-weight:700;color:#4da6ff;'>"
        "S5 Multi-Skilled Technician &nbsp;&bull;&nbsp; IND3</p>"
        "<p style='margin:2px 0;font-size:14px;font-weight:700;color:#4da6ff;'>"
        "eCommerce Fulfillment Center #8972</p>"
        f"<p style='margin:4px 0;font-size:13px;color:#374151;'>{_ADDR}</p>"
        "<p style='margin:4px 0;'>"
        "<a href='mailto:stanley.augustin@walmart.com' "
        "style='color:#4da6ff;font-size:13px;text-decoration:none;'>"
        "stanley.augustin@walmart.com</a></p>"
        f"<p style='margin:10px 0 0;'>{_WM_LOGO}</p>"
        "</div>"
    ),

    "alexander": (
        "<div style='font-family:Calibri,Arial,sans-serif;margin-top:24px;"
        "padding-top:14px;border-top:1px solid #d1d5db;line-height:1.5;'>"
        "<p style='margin:0;font-size:16px;font-style:italic;font-weight:700;"
        "color:#ffffff;'>Alexander Carr</p>"
        "<p style='margin:8px 0 0;font-size:14px;font-weight:700;color:#111;'>"
        "<span style='color:#4da6ff;'>Alexander Carr</span>"
        "&nbsp;&nbsp;Multi-Skilled Maintenance Technician</p>"
        "<p style='margin:2px 0;font-size:13px;font-weight:700;color:#111;'>"
        "Phone 317-589-1012</p>"
        "<p style='margin:8px 0;'>"
        "<a href='mailto:Alexander.Carr@walmart.com' "
        "style='color:#4da6ff;font-size:13px;font-weight:700;text-decoration:none;'>"
        "Alexander.Carr@walmart.com</a></p>"
        "<p style='margin:2px 0;font-size:13px;font-weight:700;color:#111;'>"
        "Wal-Mart EC<br>5259 W. 500 N.<br>McCordsville, In 46055</p>"
        "<p style='margin:8px 0 0;font-size:15px;font-weight:700;color:#4da6ff;'>"
        "Save money.&nbsp; Live Better.</p>"
        "</div>"
    ),

    "len": (
        "<div style='font-family:Calibri,Arial,sans-serif;margin-top:24px;"
        "padding-top:14px;border-top:1px solid #d1d5db;line-height:1.5;'>"
        "<p style='margin:0;font-size:15px;font-weight:700;color:#4da6ff;'>"
        "Len Ward</p>"
        "<p style='margin:2px 0;font-size:14px;font-weight:700;color:#4da6ff;'>"
        "S1 Multi Skill Technician &nbsp;&bull;&nbsp; IND3</p>"
        "<p style='margin:2px 0;font-size:14px;font-weight:700;color:#4da6ff;'>"
        "eCommerce Fulfillment Center #8972</p>"
        f"<p style='margin:4px 0;font-size:13px;color:#374151;'>{_ADDR}</p>"
        "<p style='margin:4px 0;'>"
        "<a href='mailto:len.ward@walmart.com' "
        "style='color:#4da6ff;font-size:13px;text-decoration:none;'>"
        "len.ward@walmart.com</a></p>"
        f"<p style='margin:10px 0 0;'>{_WM_LOGO}</p>"
        "</div>"
    ),

    "kevin": (
        "<div style='font-family:Calibri,Arial,sans-serif;margin-top:24px;"
        "padding-top:14px;border-top:1px solid #d1d5db;line-height:1.5;'>"
        "<p style='margin:0;font-size:15px;font-weight:700;color:#4da6ff;'>"
        "Kevin Smith</p>"
        "<p style='margin:2px 0;font-size:14px;font-weight:700;color:#4da6ff;'>"
        "Multi-Skilled Technician &nbsp;&bull;&nbsp; IND3</p>"
        "<p style='margin:2px 0;font-size:14px;font-weight:700;color:#4da6ff;'>"
        "eCommerce Fulfillment Center #8972</p>"
        f"<p style='margin:4px 0;font-size:13px;color:#374151;'>{_ADDR}</p>"
        "<p style='margin:4px 0;'>"
        "<a href='mailto:Kevin.Smith1@walmart.com' "
        "style='color:#4da6ff;font-size:13px;text-decoration:none;'>"
        "Kevin.Smith1@walmart.com</a></p>"
        f"<p style='margin:10px 0 0;'>{_WM_LOGO}</p>"
        "</div>"
    ),
}

# ── Fallback ────────────────────────────────────────────────────────────────────
_FALLBACK = (
    "<div style='font-family:Calibri,Arial,sans-serif;margin-top:24px;"
    "padding-top:14px;border-top:1px solid #d1d5db;line-height:1.5;'>"
    "<p style='margin:0;font-size:14px;font-weight:700;color:#4da6ff;'>"
    "IND3 Maintenance Team</p>"
    "<p style='margin:2px 0;font-size:14px;font-weight:700;color:#4da6ff;'>"
    "eCommerce Fulfillment Center #8972</p>"
    f"<p style='margin:4px 0;font-size:13px;color:#374151;'>{_ADDR}</p>"
    f"<p style='margin:10px 0 0;'>{_WM_LOGO}</p>"
    "</div>"
)


def get_signature_html(lead_name: str) -> str:
    """Return the HTML signature block for the given lead name.
    Matches case-insensitively on first name.
    Falls back to a generic IND3 block if no match found.
    """
    first = lead_name.strip().split()[0].lower() if lead_name.strip() else ""
    return _SIGS.get(first, _FALLBACK)

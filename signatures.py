"""IND3 Next Gen signature matching Isaac's real corporate Outlook signature.

Table-based, inline-style HTML for Outlook. Does not alter report sections.
The small Spark glyph is referenced by Content-ID; the EML composer must
attach ind3-walmart-spark.png as an inline MIME image with the matching CID.

Exact-name contact mapping only; never guess employee IDs or email addresses
for someone not in KNOWN.
"""
from __future__ import annotations

import html
import re

SIGNATURE_MARKER = "<!-- IND3-NEXT-GEN-SIGNATURE -->"
SPARK_CID = "ind3-walmart-spark"
FACILITY = "eCommerce Fulfillment Center # 7552/8972"
ADDRESS = "5259 W. 500 North McCordsville, Indiana, 46055"
# name -> (role, email, Walmart default/NT ID). The ID is what actually
# appears in the signature ("Full Name - defaultid"); role/email are kept
# for callers that still want them but are not rendered in this design.
KNOWN = {
    "isaac ssaku": ("Maintenance Technician", "isaac.ssaku@walmart.com", "ins0002"),
    "stanley augustin": ("Maintenance Technician", "stanley.augustin@walmart.com", "s0a00ns"),
    "alexander carr": ("Maintenance Technician", "Alexander.Carr@walmart.com", "acarr1"),
    "len ward": ("Maintenance Technician", "len.ward@walmart.com", "ldw006f"),
    "kevin smith": ("Maintenance Technician", "Kevin.Smith1@walmart.com", "k0s08q0"),
}


def get_signature_html(
    lead_name: str, shift: str = "", role: str = "", email: str = "",
    *, spark_src: str = f"cid:{SPARK_CID}",
) -> str:
    """Return a standalone signature; spark_src may be a data URL for preview.

    `shift`, `role`, and `email` are accepted for backward compatibility with
    existing callers but are intentionally not rendered -- this design is a
    plain name/facility/address block plus the Walmart tagline, matching
    Isaac's real signature exactly, not a role- or shift-specific business
    card.
    """
    name = (lead_name or "").strip() or "IND3 Maintenance Team"
    _, _, known_id = KNOWN.get(name.casefold(), ("", "", ""))
    safe_name = html.escape(name)
    id_html = f" - {html.escape(known_id)}" if known_id else ""
    safe_src = html.escape(spark_src, quote=True)
    line_style = "font-family:Georgia,'Times New Roman',serif;font-weight:700;color:#4472c4;"

    return SIGNATURE_MARKER + f"""<table role="presentation" cellspacing="0" cellpadding="0" border="0"
      style="width:100%;max-width:560px;margin:20px 0 0;border-collapse:collapse;">
      <tr><td style="{line_style}font-size:18px;line-height:24px;padding:0 0 4px;">{safe_name}{id_html}</td></tr>
      <tr><td style="{line_style}font-size:18px;line-height:24px;padding:0 0 4px;">{html.escape(FACILITY)}</td></tr>
      <tr><td style="{line_style}font-size:18px;line-height:24px;padding:0 0 10px;">{html.escape(ADDRESS)}</td></tr>
      <tr><td style="padding:0 0 6px;">
        <span style="{line_style}font-style:italic;font-size:19px;line-height:24px;">Walmart</span>
        <img src="{safe_src}" width="16" height="16" alt="Spark"
         style="display:inline-block;width:16px;height:16px;border:0;vertical-align:middle;margin-left:4px;" />
      </td></tr>
      <tr><td style="font-family:Georgia,'Times New Roman',serif;font-weight:700;
        font-style:italic;font-size:19px;line-height:24px;">
        <span style="color:#c0491e;">Save money.</span>
        <span style="color:#8b6f1a;"> Live better</span>
      </td></tr>
    </table>"""


def append_signature(
    body_html: str, lead_name: str, shift: str = "", role: str = "", email: str = ""
) -> str:
    """Append the signature once; do not modify the existing report body."""
    if SIGNATURE_MARKER in body_html:
        return body_html
    sig = get_signature_html(lead_name, shift, role, email)
    return re.sub(r"</body\s*>", lambda m: sig + m.group(0), body_html,
                  count=1, flags=re.IGNORECASE) if re.search(
                      r"</body\s*>", body_html, re.IGNORECASE
                  ) else body_html + sig


def embed_spark_in_eml(eml_bytes: bytes) -> bytes:
    """Attach the approved Spark as CID image; preserve report HTML and headers.

    Existing .eml builders produce a single text/html MIME part. Only the
    signature-bearing message is upgraded to multipart/related for Outlook.
    """
    from email import policy
    from email.message import EmailMessage
    from email.parser import BytesParser
    from pathlib import Path

    original = BytesParser(policy=policy.SMTP).parsebytes(eml_bytes)
    if original.is_multipart():
        return eml_bytes
    try:
        html_body = original.get_content()
    except (LookupError, ValueError):
        return eml_bytes
    if f"cid:{SPARK_CID}" not in html_body:
        return eml_bytes

    spark_path = Path(__file__).resolve().parent / "static" / "ind3-walmart-spark.png"
    spark_bytes = spark_path.read_bytes()
    message = EmailMessage(policy=policy.SMTP)
    for key, value in original.raw_items():
        if key.lower() not in {"mime-version", "content-type", "content-transfer-encoding"}:
            message[key] = value
    message.set_content(html_body, subtype="html", charset="utf-8", cte="base64")
    message.add_related(
        spark_bytes, maintype="image", subtype="png", cid=f"<{SPARK_CID}>",
        disposition="inline", filename="ind3-walmart-spark.png",
    )
    return message.as_bytes()

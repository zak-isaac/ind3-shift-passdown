"""IND3 Next Gen signature matching the approved reference image.

Table-based, inline-style HTML for Outlook. Does not alter report sections.
The six-ray Spark is referenced by Content-ID; the EML composer must attach
IND3_Approved_Walmart_Spark.png as an inline MIME image with the matching CID.
"""
from __future__ import annotations

import html
import re

SIGNATURE_MARKER = "<!-- IND3-NEXT-GEN-SIGNATURE -->"
SPARK_CID = "ind3-walmart-spark"
ADDRESS = "5259 W. 500 N., McCordsville, IN 46055"
KNOWN = {
    "isaac ssaku": ("Maintenance Technician", "isaac.ssaku@walmart.com"),
    "stanley augustin": ("Maintenance Technician", "stanley.augustin@walmart.com"),
    "alexander carr": ("Maintenance Technician", "Alexander.Carr@walmart.com"),
    "len ward": ("Maintenance Technician", "len.ward@walmart.com"),
    "kevin smith": ("Maintenance Technician", "Kevin.Smith1@walmart.com"),
}


def get_signature_html(
    lead_name: str, shift: str = "", role: str = "", email: str = "",
    *, spark_src: str = f"cid:{SPARK_CID}",
) -> str:
    """Compact Outlook signature matching the Calibri passdown email.

    No oversized bordered card, faux business card, or duplicate report
    heading. The actual report stays untouched; the signoff is appended once.
    """
    name = (lead_name or "").strip() or "IND3 Maintenance Reliability"
    known_role, known_email = KNOWN.get(name.casefold(), ("", ""))
    title = (role or known_role or "Maintenance Reliability").strip()
    if shift:
        title += f" | {shift.strip()}"
    safe_name = html.escape(name)
    safe_title = html.escape(title)
    src = html.escape(spark_src, quote=True)
    mail = (email or known_email).strip()
    mail_html = ""
    if mail:
        safe_mail = html.escape(mail, quote=True)
        mail_html = (
            f'<tr><td colspan="2" style="padding:3px 0 0 0;font-size:12px;'
            f'line-height:17px;"><a href="mailto:{safe_mail}" '
            f'style="color:#0053e2;text-decoration:none;">{safe_mail}</a></td></tr>'
        )
    return SIGNATURE_MARKER + f"""<table role="presentation" cellspacing="0" cellpadding="0" border="0"
    style="width:100%;max-width:700px;margin:20px 0 0;border-top:1px solid #cfd8e3;
    border-collapse:collapse;font-family:Calibri,Arial,sans-serif;color:#263d55;">
    <tr><td colspan="2" style="padding:12px 0 1px;font-size:14px;
    line-height:20px;font-weight:bold;">{safe_name}</td></tr>
    <tr><td colspan="2" style="padding:0 0 3px;font-size:13px;
    line-height:18px;color:#42576e;">{safe_title}</td></tr>
    {mail_html}
    <tr>
      <td style="width:20px;padding:9px 5px 0 0;vertical-align:middle;">
        <img src="{src}" width="17" height="17" alt="Walmart Spark"
        style="display:block;width:17px;height:17px;border:0;" />
      </td>
      <td style="padding:9px 0 0;vertical-align:middle;font-size:11px;
      line-height:15px;color:#61738a;">Walmart &middot; IND3 &middot;
      NEXT GEN FULFILLMENT &middot; Maintenance Operations</td>
    </tr>
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

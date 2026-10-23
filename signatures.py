"""IND3 Next Gen signature matching the approved reference image.

Table-based, inline-style HTML for Outlook. Does not alter report sections.
The six-ray Spark is referenced by Content-ID; the EML composer must attach
IND3_Approved_Walmart_Spark.png as an inline MIME image with the matching CID.

Ported from the personal-GitHub operational-integration branch
(github.com/zak-isaac/ind3-shift-passdown, commits bebce11/85c4c77).
Replaces the old hardcoded-per-MST signature blocks wholesale -- this is an
approved brand-reference redesign, not something to hand-merge with the
prior design.
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
    """Return a standalone signature; spark_src may be a data URL for preview.

    Exact-name contact mapping only; never guess employee email addresses.
    """
    name = (lead_name or "").strip() or "IND3 Maintenance Team"
    known_role, known_email = KNOWN.get(name.casefold(), ("", ""))
    title = (role or known_role or "IND3 Team Member").strip()
    shift = (shift or "").strip()
    if shift:
        title += f" | {shift}"
    mail = (email or known_email).strip()
    safe_name, safe_title = html.escape(name), html.escape(title)
    safe_src = html.escape(spark_src, quote=True)
    mail_row = ""
    if mail:
        safe_mail = html.escape(mail, quote=True)
        mail_row = (
            '<tr><td style="padding:0 0 11px;color:#344154;font-size:17px;'
            'line-height:22px;">'
            f'<a href="mailto:{safe_mail}" style="color:#344154;'
            f'text-decoration:none;">{safe_mail}</a></td></tr>'
        )

    return SIGNATURE_MARKER + f"""<table role="presentation" cellspacing="0" cellpadding="0" border="0"
      style="width:100%;max-width:612px;background:#ffffff;border:1px solid #e4e7eb;
      border-radius:16px;border-collapse:separate;font-family:Arial,Calibri,sans-serif;">
      <tr><td style="padding:25px 22px 23px 23px;">
        <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
          <tr>
            <td width="4" bgcolor="#0053E2" style="width:4px;min-width:4px;background:#0053E2;">&nbsp;</td>
            <td style="padding-left:19px;">
              <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                <tr><td style="font-size:28px;line-height:34px;font-weight:700;
                  color:#172B4D;padding:0 0 16px;">{safe_name}</td></tr>
                <tr><td style="font-size:20px;line-height:26px;font-weight:700;
                  color:#0053E2;padding:0 0 13px;">{safe_title}</td></tr>
                <tr><td style="font-size:18px;line-height:24px;color:#6B778C;
                  padding:0 0 23px;">IND3 &middot; Next Generation Fulfillment Center #8972</td></tr>
                {mail_row}
                <tr><td style="font-size:17px;line-height:22px;color:#465367;
                  padding:0 0 27px;">{html.escape(ADDRESS)}</td></tr>
                <tr><td style="border-top:1px solid #dce2ea;padding:25px 0 0;">
                  <table role="presentation" cellspacing="0" cellpadding="0" border="0" width="100%">
                    <tr>
                      <td width="44" valign="middle" style="width:44px;padding-right:7px;">
                        <img src="{safe_src}" width="38" height="38" alt="Walmart Spark"
                         style="display:block;width:38px;height:38px;border:0;" />
                      </td>
                      <td valign="middle" style="white-space:nowrap;">
                        <div style="font-size:21px;line-height:25px;font-weight:700;color:#0053E2;">Walmart</div>
                        <div style="font-size:15px;line-height:20px;letter-spacing:.25px;
                          color:#68768C;">NEXT GEN FULFILLMENT</div>
                      </td>
                      <td align="right" valign="middle" style="font-size:15px;line-height:20px;
                        color:#68768C;white-space:nowrap;padding-left:12px;">
                        Maintenance Operations
                      </td>
                    </tr>
                  </table>
                </td></tr>
              </table>
            </td>
          </tr>
        </table>
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

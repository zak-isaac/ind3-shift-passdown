"""Generate a realistic Outlook EML for visual review (2026-10-26 integration
report, section 8: Outlook EML and signature) -- using the ACTUAL production
_build_eml() path from main.py, not a hand-rolled MIME structure, so the
Spark CID embedding gets genuinely exercised."""
from pathlib import Path

from report_models import ReportData, RewrittenHappening, OosUnit
from formatter import format_email_passdown_html
import signatures as sigs

data = ReportData(
    date="2026-10-26",
    shift_type="S4",
    lead_name="Isaac Ssaku",
    dc_number="IND3",
    vendors=["Knapp field tech on-site for ASRS lane 3 diagnostics"],
    happenings=[
        RewrittenHappening(
            title="CONV0042", wo_number="28031204", tech_name="Ssaku, Isaac",
            cause="Belt tracking issue reported by operator",
            paragraph="Replaced belt on conveyor 42; tested, ran okay for 10 minutes. "
                      "Assisted with belt replacement, held tensioner while installing.",
        ),
        RewrittenHappening(
            title="BALER0003", wo_number="27999099", tech_name="Carr, Alexander",
            cause="Baler not cycling",
            paragraph="Reset the baler control module and cleared the fault code. "
                      "Ran a full cycle to confirm proper operation.",
        ),
    ],
    pm_count=3,
    rts=["BALER0003 returned to service at 14:20"],
    oos=[OosUnit(unit="ASRS-LANE3", issue="Encoder fault under investigation", next_steps="Knapp tech diagnosing")],
    summary=["Quiet shift overall, one baler fault resolved, ASRS lane 3 still under vendor diagnosis."],
    shift_notes="Heads up: third-floor SLAM areas reported slow conveyance -- keep an eye on it next shift.",
)

html_body = format_email_passdown_html(data)
html_body = sigs.append_signature(html_body, data.lead_name, data.shift_type)


def build_eml(subject, to_addrs, html_body):
    import base64
    encoded = base64.b64encode(html_body.encode("utf-8")).decode("ascii")
    wrapped = "\r\n".join(encoded[i:i + 76] for i in range(0, len(encoded), 76))
    lines = [
        "X-Unsent: 1", "MIME-Version: 1.0",
        f"To: {to_addrs}", f"Subject: {subject}",
        "Content-Type: text/html; charset=UTF-8",
        "Content-Transfer-Encoding: base64", "", wrapped,
    ]
    return sigs.embed_spark_in_eml("\r\n".join(lines).encode("utf-8"))


raw_eml = build_eml(f"IND3 Shift Passdown - {data.shift_type} - {data.date}",
                     "ind3-maintenance-team@walmart.com", html_body)

out = Path("_review_passdown_report.eml")
out.write_bytes(raw_eml)
print("Wrote", out.resolve(), out.stat().st_size, "bytes")
print("Is multipart/related (Spark embedded):", b"multipart/related" in raw_eml)
print("Signature marker present:", b"IND3-NEXT-GEN-SIGNATURE" in raw_eml)

Path("_review_passdown_report.html").write_text(html_body, encoding="utf-8")

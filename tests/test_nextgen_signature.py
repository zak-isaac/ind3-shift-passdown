"""The Next Gen signature is the only addition to passdown HTML."""
import base64
from email import policy
from email.parser import BytesParser

import signatures


def test_signature_does_not_modify_existing_report():
    report = "<html><body><h1>S2 Passdown</h1><p>LOTO pending</p></body></html>"
    output = signatures.append_signature(report, "Isaac Ssaku", "S2")
    assert output.startswith(report.split("</body>")[0])
    assert "Maintenance Technician | S2" in output
    assert "NEXT GEN FULFILLMENT" in output
    assert 'cid:ind3-walmart-spark' in output
    assert output.count(signatures.SIGNATURE_MARKER) == 1
    assert signatures.append_signature(output, "Isaac Ssaku", "S2") == output


def test_spark_is_embedded_as_inline_cid_in_eml():
    html = signatures.append_signature("<html><body>Original report</body></html>", "Isaac Ssaku", "S2")
    b64 = base64.b64encode(html.encode("utf-8")).decode("ascii")
    eml = (
        "X-Unsent: 1\r\nTo: sample@example.com\r\nSubject: S2 Passdown\r\n"
        'MIME-Version: 1.0\r\nContent-Type: text/html; charset="utf-8"\r\n'
        f"Content-Transfer-Encoding: base64\r\n\r\n{b64}\r\n"
    ).encode("utf-8")
    result = BytesParser(policy=policy.default).parsebytes(signatures.embed_spark_in_eml(eml))
    assert result["X-Unsent"] == "1"
    assert result["To"] == "sample@example.com"
    parts = list(result.walk())
    assert [p.get_content_type() for p in parts] == [
        "multipart/related", "text/html", "image/png",
    ]
    assert parts[1].get_content().replace("\r\n", "\n").strip() == html
    assert parts[2]["Content-ID"] == "<ind3-walmart-spark>"
    assert parts[2].get_payload(decode=True).startswith(b"\x89PNG")


def test_eml_without_signature_remains_unchanged():
    original = b"Subject: Test\r\nContent-Type: text/plain\r\n\r\nUnchanged"
    assert signatures.embed_spark_in_eml(original) == original

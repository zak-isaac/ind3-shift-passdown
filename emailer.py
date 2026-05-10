"""Email sender – sends the passdown report via Office 365 SMTP.

Configure via environment variables (or a .env file):

    PASSDOWN_EMAIL_FROM=you@wal-mart.com
    PASSDOWN_EMAIL_PASS=your-app-password
    PASSDOWN_EMAIL_TO=supervisor@wal-mart.com,nextlead@wal-mart.com
"""
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.office365.com"
SMTP_PORT = 587


def _cfg(key: str) -> str:
    val = os.environ.get(key, "").strip()
    return val


def is_configured() -> bool:
    """True when all three required env vars are set."""
    return bool(_cfg("PASSDOWN_EMAIL_FROM")
                and _cfg("PASSDOWN_EMAIL_PASS")
                and _cfg("PASSDOWN_EMAIL_TO"))


def send_passdown(subject: str, body: str) -> None:
    """Send *body* (plain-text passdown report) to the configured recipients.

    Raises RuntimeError if env vars are missing.
    Raises smtplib.SMTPException on send failure.
    """
    from_addr = _cfg("PASSDOWN_EMAIL_FROM")
    password  = _cfg("PASSDOWN_EMAIL_PASS")
    to_addrs  = [a.strip() for a in _cfg("PASSDOWN_EMAIL_TO").split(",") if a.strip()]

    if not (from_addr and password and to_addrs):
        raise RuntimeError(
            "Email not configured. Set PASSDOWN_EMAIL_FROM, "
            "PASSDOWN_EMAIL_PASS, and PASSDOWN_EMAIL_TO."
        )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = ", ".join(to_addrs)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.login(from_addr, password)
        s.sendmail(from_addr, to_addrs, msg.as_bytes())

"""Email sender – sends the passdown report via Office 365 SMTP.

Configure via .env file (auto-loaded at startup) or environment variables:

    PASSDOWN_EMAIL_FROM=you@wal-mart.com
    PASSDOWN_EMAIL_PASS=your-outlook-password
    PASSDOWN_EMAIL_TO=supervisor@wal-mart.com  # optional fallback

The from/to addresses are managed via the Admin panel and stored in the DB.
Only the password stays in .env — never in the DB or the UI.
"""
import os
import smtplib
import ssl
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

SMTP_HOST = "smtp.office365.com"
SMTP_PORT = 587


def _cfg(key: str) -> str:
    return os.environ.get(key, "").strip()


def is_password_set() -> bool:
    """True when the SMTP password env var is present."""
    return bool(_cfg("PASSDOWN_EMAIL_PASS"))


def is_configured(from_addr: str = "", to_raw: str = "") -> bool:
    """True when we have everything needed to actually send."""
    _from = from_addr or _cfg("PASSDOWN_EMAIL_FROM")
    _to   = to_raw   or _cfg("PASSDOWN_EMAIL_TO")
    return bool(_from and _cfg("PASSDOWN_EMAIL_PASS") and _to)


def send_passdown(
    subject: str,
    body: str,
    *,
    from_addr: str = "",
    to_addrs: list[str] | None = None,
) -> None:
    """Send *body* (plain-text passdown) to recipients.

    Args:
        from_addr: Override the PASSDOWN_EMAIL_FROM env var (from DB setting).
        to_addrs:  Override the PASSDOWN_EMAIL_TO env var (from DB setting).

    Raises RuntimeError if config is incomplete.
    Raises smtplib.SMTPException on send failure.
    """
    _from    = from_addr or _cfg("PASSDOWN_EMAIL_FROM")
    password = _cfg("PASSDOWN_EMAIL_PASS")
    _to      = to_addrs or [a.strip() for a in _cfg("PASSDOWN_EMAIL_TO").split(",") if a.strip()]

    if not _from:
        raise RuntimeError("No sender address. Set it in Admin → Email Settings.")
    if not password:
        raise RuntimeError("PASSDOWN_EMAIL_PASS not set in .env file.")
    if not _to:
        raise RuntimeError("No recipients. Set them in Admin → Email Settings.")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = _from
    msg["To"]      = ", ".join(_to)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    ctx = ssl.create_default_context()
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as s:
        s.ehlo()
        s.starttls(context=ctx)
        s.login(_from, password)
        s.sendmail(_from, _to, msg.as_bytes())

"""Send emails via the locally installed Outlook app using PowerShell COM automation.

No passwords. No SMTP auth. No Azure registration.
Outlook is already authenticated with the work account on this machine.
PowerShell tells Outlook to send — it just works.
"""
import asyncio
import subprocess
import tempfile
import os


def _build_ps_script(to_addrs: list[str], subject: str, from_name: str, reply_to: str) -> str:
    """Build a PowerShell script that sends via Outlook COM.

    Strategy: create a blank MailItem first (Outlook auto-inserts the default
    signature into HTMLBody), then inject our passdown content at the very top
    of that body so the signature is preserved beneath it.
    """
    to_str = "; ".join(to_addrs)
    subj   = subject.replace("'", "''")
    reply  = reply_to.replace("'", "''")
    return f"""
$ErrorActionPreference = 'Stop'
$ol   = New-Object -ComObject Outlook.Application
$mail = $ol.CreateItem(0)
$mail.To      = '{to_str}'
$mail.Subject = '{subj}'

# Read our passdown HTML
$ourFull = [System.IO.File]::ReadAllText($env:PASSDOWN_BODY_FILE)

# Strip outer <html>/<body> wrappers so we get just the inner content
$ourInner = [System.Text.RegularExpressions.Regex]::Replace(
    $ourFull, '(?is)<html[^>]*>.*?<body[^>]*>', '')
$ourInner = [System.Text.RegularExpressions.Regex]::Replace(
    $ourInner, '(?is)</body>.*?</html>', '')

# Outlook auto-filled HTMLBody with the default MST signature.
# We inject our content right after the opening <body> tag.
$sigBody = $mail.HTMLBody
$injected = [System.Text.RegularExpressions.Regex]::Replace(
    $sigBody,
    '(?i)(<body[^>]*>)',
    "`$1`n$ourInner`n<hr style='border:none;border-top:1px solid #e5e7eb;margin:16px 0;'>`n"
)
$mail.HTMLBody = $injected

if ('{reply}') {{
    $mail.ReplyRecipients.Add('{reply}') | Out-Null
}}
$mail.Send()
Write-Host 'OK'
"""


def is_outlook_available() -> bool:
    """Quick check — can we create an Outlook COM object?"""
    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-Command",
             "New-Object -ComObject Outlook.Application | Out-Null; Write-Host 'OK'"],
            capture_output=True, text=True, timeout=8,
        )
        return result.returncode == 0 and "OK" in result.stdout
    except Exception:
        return False


def get_sender_address() -> str:
    """Return the primary SMTP address of the logged-in Outlook account."""
    try:
        result = subprocess.run(
            ["powershell", "-NonInteractive", "-Command",
             "(New-Object -ComObject Outlook.Application).Session.CurrentUser.AddressEntry.GetExchangeUser().PrimarySmtpAddress"],
            capture_output=True, text=True, timeout=8,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


async def send_email(
    to_addrs: list[str],
    subject: str,
    body: str,
    from_name: str = "IND3 Shift Passdown",
    reply_to: str = "",
) -> None:
    """Send an email via Outlook COM. Raises RuntimeError on failure."""

    def _send():
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(body)
            tmp_path = f.name
        try:
            env = os.environ.copy()
            env["PASSDOWN_BODY_FILE"] = tmp_path
            script = _build_ps_script(to_addrs, subject, from_name, reply_to)
            result = subprocess.run(
                ["powershell", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=30, env=env,
            )
            if result.returncode != 0 or "OK" not in result.stdout:
                raise RuntimeError(
                    result.stderr.strip() or result.stdout.strip() or "Outlook send failed"
                )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    await asyncio.to_thread(_send)

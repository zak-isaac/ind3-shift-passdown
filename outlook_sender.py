"""Send or display emails via the locally installed Outlook app using PowerShell COM.

No passwords. No SMTP auth. No Azure registration.
Outlook is already authenticated with the work account on this machine.
"""
import asyncio
import subprocess
import tempfile
import os


def _build_ps_script(to_addrs: list[str], subject: str, reply_to: str, display_only: bool = False) -> str:
    """Build a PowerShell script that creates an Outlook mail item.

    display_only=True  → .Display()  — opens the compose window for the lead
    display_only=False → .Send()     — sends silently in the background
    """
    to_str  = "; ".join(to_addrs)
    subj    = subject.replace("'", "''")
    reply   = reply_to.replace("'", "''")
    action  = "$mail.Display()" if display_only else "$mail.Send()"
    return f"""
$ErrorActionPreference = 'Stop'
$ol   = New-Object -ComObject Outlook.Application
$mail = $ol.CreateItem(0)
$mail.To      = '{to_str}'
$mail.Subject = '{subj}'

# Read our passdown HTML from temp file
$ourFull = [System.IO.File]::ReadAllText($env:PASSDOWN_BODY_FILE)

# Strip outer <html>/<body> wrappers — keep only inner content
$ourInner = [System.Text.RegularExpressions.Regex]::Replace(
    $ourFull, '(?is)<html[^>]*>.*?<body[^>]*>', '')
$ourInner = [System.Text.RegularExpressions.Regex]::Replace(
    $ourInner, '(?is)</body>.*?</html>', '')

# Outlook auto-fills HTMLBody with the lead's saved signature.
# Inject our content right after the opening <body> tag so the
# signature stays underneath, exactly as the lead expects.
$sigBody  = $mail.HTMLBody
$injected = [System.Text.RegularExpressions.Regex]::Replace(
    $sigBody,
    '(?i)(<body[^>]*>)',
    "`$1`n$ourInner`n<hr style='border:none;border-top:1px solid #e5e7eb;margin:16px 0;'>`n"
)
$mail.HTMLBody = $injected

if ('{reply}') {{
    $mail.ReplyRecipients.Add('{reply}') | Out-Null
}}
{action}
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
    """Send an email silently via Outlook COM. Raises RuntimeError on failure."""
    await _run_outlook_script(to_addrs, subject, reply_to, body, display_only=False)


async def display_email(
    to_addrs: list[str],
    subject: str,
    body: str,
    reply_to: str = "",
) -> None:
    """Open Outlook compose window pre-filled with the passdown data.

    The lead’s own saved signature is preserved beneath the content.
    They can edit freely and click Send themselves — email goes from
    their own account, not the app.
    """
    await _run_outlook_script(to_addrs, subject, reply_to, body, display_only=True)


async def _run_outlook_script(
    to_addrs: list[str],
    subject: str,
    reply_to: str,
    body: str,
    display_only: bool,
) -> None:
    """Shared worker: write body to temp file, run PS script, clean up."""
    def _run():
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(body)
            tmp_path = f.name
        try:
            env = os.environ.copy()
            env["PASSDOWN_BODY_FILE"] = tmp_path
            script = _build_ps_script(to_addrs, subject, reply_to, display_only)
            result = subprocess.run(
                ["powershell", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=30, env=env,
            )
            if result.returncode != 0 or "OK" not in result.stdout:
                raise RuntimeError(
                    result.stderr.strip() or result.stdout.strip() or "Outlook action failed"
                )
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    await asyncio.to_thread(_run)

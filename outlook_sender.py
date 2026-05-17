"""Send emails via the locally installed Outlook app using PowerShell COM automation.

No passwords. No SMTP auth. No Azure registration.
Outlook is already authenticated with the work account on this machine.
PowerShell tells Outlook to send — it just works.
"""
import asyncio
import subprocess
import tempfile
import os


def _build_ps_script(to_addrs: list[str], subject: str, body: str, from_name: str) -> str:
    """Build a PowerShell script that sends via Outlook COM."""
    # Write body to a temp file to avoid any escaping nightmares with long text
    to_str = "; ".join(to_addrs)
    # Escape only what's needed inline (subject is usually short)
    subj = subject.replace("'", "''")
    return f"""
$ErrorActionPreference = 'Stop'
$ol   = New-Object -ComObject Outlook.Application
$mail = $ol.CreateItem(0)
$mail.To      = '{to_str}'
$mail.Subject = '{subj}'
$mail.Body    = [System.IO.File]::ReadAllText($env:PASSDOWN_BODY_FILE)
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


async def send_email(
    to_addrs: list[str],
    subject: str,
    body: str,
    from_name: str = "IND3 Shift Passdown",
) -> None:
    """Send an email via Outlook COM. Raises RuntimeError on failure."""

    def _send():
        # Write body to a temp file — avoids all escaping issues with long text
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write(body)
            tmp_path = f.name

        try:
            env = os.environ.copy()
            env["PASSDOWN_BODY_FILE"] = tmp_path
            script = _build_ps_script(to_addrs, subject, body, from_name)
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

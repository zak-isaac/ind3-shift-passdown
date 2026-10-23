# IND3 Passdown Server — Deployment & Ops Runbook

Step-by-step install/verify/troubleshoot guide for the shop-floor PC hosting
this app. Written for whoever is standing at the machine with an admin
account, not necessarily a developer.

## Prerequisites

- Windows PC with admin rights available (you'll need them once, for install)
- Python 3.13 already set up in `.venv` inside the project folder
- [Ollama](https://ollama.com) installed and running, with at least one model
  pulled (`llama3.1:8b` or `phi4-mini`) — the app auto-detects whichever is
  available
- Outlook installed and configured for the account that will send passdown
  emails (COM automation reads the active Outlook profile — no password needed)
- Port `8766` free on the machine

## First-Time Install

1. Open **PowerShell as Administrator** (right-click Start → "Terminal (Admin)").
2. Navigate to the project folder:
   ```powershell
   cd "C:\Users\ins0002\Documents\puppy_workspace\IND3-Passdown-Server"
   ```
3. Run the installer:
   ```powershell
   .\install_service.ps1
   ```
4. It will print something like:
   ```
   Task name  : IND3PassdownServer
   Runs as    : NT AUTHORITY\SYSTEM
   Triggers   : Boot, hibernate resume, session connect
   Restarts   : every 1 min on crash (up to 99x)
   State now  : Running
   Access URLs:
     This PC   : http://localhost:8766
     Other PCs : http://<this-PC's-IP>:8766
   ```
5. Open `http://localhost:8766` in a browser to confirm it loads.

That's it. The task now survives reboots, hibernation, and user logoff —
nobody needs to be logged into Windows for the server to run.

## Verifying It's Actually Running (Health Check)

```powershell
# Is something listening on the port?
Get-NetTCPConnection -LocalPort 8766 -State Listen

# What does the scheduled task think its state is?
Get-ScheduledTask -TaskName IND3PassdownServer | Select-Object TaskName, State

# Tail the last 20 lines of the log
Get-Content "C:\Users\ins0002\Documents\puppy_workspace\IND3-Passdown-Server\server.log" -Tail 20
```

A healthy startup looks like this in the log:
```
INFO:     Application startup complete.
INFO:     Uvicorn running on http://0.0.0.0:8766 (Press CTRL+C to quit)
[ai_rewriter] Warming up '<model name>'...
```

## Common Problems

### "Only one usage of each socket address" / server keeps crash-looping

This means **something else already owns port 8766** — usually a leftover
process from a previous install that was never cleaned up. The scheduled
task will crash-loop every 5 seconds forever until the port is freed.

**Diagnose:**
```powershell
Get-NetTCPConnection -LocalPort 8766 -State Listen
```
Note the PID it returns, then check how long it's been running:
```powershell
Get-Process -Id <PID> | Select-Object Id, StartTime
```
If that start time is from days/weeks ago and doesn't match your latest
install, it's a zombie.

**Fix (as Administrator):**
```powershell
Stop-Process -Id <PID> -Force
```
Within about 5 seconds the scheduled task's own restart loop will notice the
port is free and bind to it correctly. Confirm with the health check above.

> **Why this happens:** if an old scheduled task or startup script was set up
> before `install_service.ps1` existed, running the new installer does *not*
> automatically kill old processes it doesn't know about. Always check for
> and remove old duplicate tasks:
> ```powershell
> Get-ScheduledTask | Where-Object {$_.TaskName -like '*IND3*' -or $_.TaskName -like '*Passdown*'}
> ```

### Code changes don't seem to take effect

The Python process only reads code at startup. Editing `.py` files while the
service is running does nothing until it restarts. After any code change:
```powershell
Stop-Process -Id (Get-NetTCPConnection -LocalPort 8766 -State Listen).OwningProcess -Force
```
Wait ~5-10 seconds for the scheduled task's auto-restart to pick it back up,
then re-verify with the health check.

### AI rewriting isn't happening / reports look like raw technician text

Check Ollama is actually running and has a model installed:
```powershell
curl http://localhost:11434/api/tags
```
If this fails to connect, start Ollama. If it connects but returns an empty
`models` list, pull one:
```powershell
ollama pull llama3.1:8b
```
The app auto-detects on startup — restart the service after installing a
new model (see above).

### Outlook email won't send / "Compose Email" doesn't produce anything usable

- Confirm Outlook is installed and set as the default mail client on this
  machine.
- Confirm the machine has an active Outlook profile signed in — COM
  automation borrows the logged-in Outlook session.
- The generated `.eml` file lands in the Downloads folder of whichever
  Windows user account the browser session belongs to — check there first.

### Someone needs to be added to / removed from the roster

Roster lives in an Excel file (`Drax  Associates.xlsx`, loaded by
`associates.py`). Edit it directly, then either:
- Use the **Admin panel** in-app (`/admin`) → reload roster button, or
- Restart the service to force a fresh load at startup.

## Removing / Reinstalling the Service

```powershell
Unregister-ScheduledTask -TaskName IND3PassdownServer -Confirm:$false
```
Then re-run `install_service.ps1` to set it back up clean.

## Backing Up Data

All shift/entry/report history lives in a single SQLite file:
```
dbs\ind3.db
```
Back this up regularly (it's small — copy it anywhere, even while the
service is running; SQLite handles concurrent reads fine). There's no
automatic backup job configured today — this is a manual process until one
gets built.

## What NOT to Touch

- **`registry.db`** — facility catalogue. This deployment is hardcoded to
  operate on IND3; don't switch the active facility unless you mean to.
- **`.env`** — has an SMTP password field that's intentionally left blank.
  Outlook COM automation is the email path in use; don't populate this
  unless deliberately switching email delivery methods.
- **`assets.json`** — the master list of valid asset IDs. Regenerate from
  the source Excel export rather than hand-editing.

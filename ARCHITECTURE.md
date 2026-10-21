# IND3 Passdown Server — Architecture

Technical reference for whoever maintains this app after it goes to production.
For end-user instructions (how to log in, upload timecards, send the passdown
email), see the in-app guide at `/guide` — this document is for the person on
call when something breaks.

## What This Is

A FastAPI web app that runs the shift handoff process for the IND3 Reliability
team: technicians log work-order entries during their shift, the MST (lead)
reviews them, and at shift end an AI-assisted passdown report gets emailed to
the management distribution list.

**Stack:** Python 3.13 + FastAPI + Jinja2 + SQLite + HTMX (no JS framework,
no build step) + a local Ollama LLM for report polish.

## High-Level Flow

```
Tech uploads Cetaris   Tech types a
timecard (CSV/XLSX)    manual entry
        │                    │
        └──────────┬─────────┘
                    ▼
         entries table (SQLite)
                    │
         WebSocket push ──────► MST dashboard (live feed)
                    │
        MST clicks "Generate Report"
                    │
                    ▼
      wo_consolidator.py — group by WO#, then by asset ID
                    │
                    ▼
       ai_rewriter.py — polish each remark via local LLM
                    │
                    ▼
        formatter.py — build HTML preview + email body
                    │
                    ▼
      MST clicks "Compose Email" → .eml file → opens in Outlook → Send
                    │
                    ▼
           MST clicks "End Shift" → shift locked, moved to History
```

## Directory Map

| Path | Purpose |
|---|---|
| `main.py` | FastAPI app, all routes (~50 endpoints). Single file by design — see note below. |
| `db.py` | SQLite access layer for the active facility's database (`dbs/{slug}.db`). |
| `registry.py` | Multi-facility catalogue (`registry.db`). Tracks which facility is "active" and geofencing for facility-switching. |
| `wo_consolidator.py` | Groups raw entries by Work Order number, then by Asset ID. Pure Python, no AI — deterministic. |
| `ai_rewriter.py` | Talks to local Ollama, rewrites individual remarks into professional language. Has typo-fix, garbage-detection, and raw-text fallback safety nets. |
| `rewriter.py` | Legacy/simple single-happening rewrite path used by `/shift/end` when no lead-generated report exists yet. Template-based fallback if AI unavailable. |
| `summarizer.py` | Stub for Walmart's Element AI (`gpt-4o`) — not currently active, needs an API key. Used for merged multi-tech remark synthesis if enabled. |
| `formatter.py` | Turns `ReportData` into HTML preview, plain-text email, and rich HTML email (with PM table, LOTO status, signature). |
| `cetaris_parser.py` | Parses Cetaris CSV/XLSX exports into structured entries. |
| `pm_pending_parser.py` | Parses the "PMs needing completion" Cetaris export. |
| `exporters.py` | DOCX of the passdown report. |
| `emailer.py` / `outlook_sender.py` | Email delivery — Outlook COM automation (no SMTP password needed) is the primary path; `emailer.py` is an SMTP fallback. |
| `associates.py` | Loads the technician/MST roster from an Excel file (`Drax  Associates.xlsx`), keyed by Walmart associate number. |
| `signatures.py` | Pulls the logged-in user's actual Outlook signature to append to the passdown email. |
| `report_models.py` | Pydantic models: `ReportData`, `RewrittenHappening` (has `sub_points` for nested multi-WO bullets), `HappeningInput`, `OosUnit`. |
| `templates/*.html` | Jinja2 templates — one per page. Styled with Tailwind, HTMX for live updates (no page reloads). |
| `dbs/{slug}.db` | Per-facility SQLite database. IND3's active data lives in `dbs/ind3.db`. |
| `registry.db` | Facility catalogue + which facility is currently active. |
| `assets.json` | Static list of every valid asset ID (~1700), sourced from an Excel export. |

### Why `main.py` is 125KB / one file

This has grown large. It works, and splitting it purely to hit a line count
would hurt more than help right now given how interlinked the shift/entry/report lifecycle is. If it keeps growing, the next logical split is by router:
`routes_tech.py`, `routes_lead.py`, `routes_admin.py`, `routes_history.py`,
mounted as FastAPI `APIRouter`s. Not urgent for launch.

## Data Model (per-facility SQLite)

| Table | Purpose |
|---|---|
| `shifts` | One row per shift. Up to 2 active simultaneously (overlap periods). |
| `entries` | Every tech-logged work item. Never deleted — full history retained. Has `source` (`manual`/`cmms`), `wo_number`, `raw_remarks` (JSON), `excluded`, `carry_over`, `priority`. |
| `reports` | Snapshot of every generated report per shift, plus the serialized `ReportData` JSON for exact re-formatting. |
| `oos_units` | Currently out-of-service equipment — drives the red banner on tech dashboards. |
| `rts_log` | History of units returned to service. |
| `pm_wos` | PM work orders completed this shift (auto-counted). |
| `pending_pms` | Parsed from the "PMs needing completion" upload — what's due, when. |
| `pm_files` / `happenings_files` | Uploaded source files, kept for re-download/audit. |
| `associates` | Local cache of the roster (synced from Excel). |
| `tech_done` | Tracks which technicians have signed off for the shift. |
| `ot_codes` | Overtime request codes (adjacent feature, not core to passdown). |
| `settings` | Misc key/value config (LOTO, email addresses, etc.) |

**Consolidation is view-only.** Raw `entries` rows are never modified or merged
in the database — `wo_consolidator.py` groups them only at report-generation
time. This means you can always trace a report line back to the exact
technician entries that produced it.

## The AI Rewrite Pipeline

Full detail lives in the code comments in `ai_rewriter.py`, but the short version:

1. **Local only** — Ollama running on `localhost:11434`. No cloud LLM calls
   for this step (Element/gpt-4o integration exists in `summarizer.py` but is
   currently unused — needs an API key to activate).
2. **Model auto-selection** at startup, priority order:
   `phi4:14b` → `llama3.1:8b` → `phi4-mini`. Whichever's installed and highest
   in that list wins. Check what's actually running with:
   ```
   curl http://localhost:11434/api/tags
   ```
3. **One remark per AI call** (not batched) — smaller local models handle
   focused single-entry rewrites more reliably than long batch prompts.
4. **Typo-fix pass** before the AI ever sees the text (`_fix_typos`) —
   catches known warehouse-specific typos deterministically.
5. **Safety nets on the way out**: strip any echoed asset-ID prefix, void
   vague filler phrases ("assisted with the repair" → discarded), detect
   garbage output, and — critically — **fall back to the raw (typo-fixed)
   remark if the AI fails or times out**, rather than dropping the entry.
   90-second timeout per call.
6. **Re-grouping after AI** happens in `main.py` (`/lead/generate`) — remarks
   are grouped back into `sub_points` by asset ID so an asset touched by
   multiple Work Orders renders as nested bullets, one per WO.

If Ollama is down entirely, the report still generates — just without the
language polish (raw remarks pass straight through).

## Auth Model

There are no traditional passwords. Everyone authenticates with their
**Walmart associate number**:
- **Techs** — associate number only, looked up against the `associates` roster.
- **MST/Lead** — associate number acts as a PIN to unlock `/lead`. A manager
  bypass code exists for emergencies (see `main.py` around `_lead_redirect`
  and the manager-bypass roster entry).
- Sessions are cookie-based, scoped per shift (a tech's cookie is bound to a
  specific `shift_id` so overlapping shifts don't cross-contaminate).

There is **no admin login separate from the MST PIN** — the `/admin` panel
uses the same lead-auth check. Treat associate numbers as sensitive.

## Multi-Facility Support (`registry.py`)

The app ships pre-seeded with ~30 Walmart facility slugs (IND1, IND2, IND3,
ARK1, etc.) in `registry.db`. Only one facility is "active" at a time — that's
what determines which `dbs/{slug}.db` file `db.py` reads and writes.
**This IND3 deployment always operates against `dbs/ind3.db`.** Facility
switching exists for potential reuse at other sites but is not part of the
IND3 production workflow — don't touch it unless you're intentionally
repointing the server at a different facility.

## Deployment

This app is **not** deployed through AI Launchpad / AI Innovation Lab — it
runs as a native Windows Scheduled Task directly on a shop-floor PC.

- **Task name:** `IND3PassdownServer`
- **Runs as:** `NT AUTHORITY\SYSTEM` (survives logoff, no user needs to be signed in)
- **Triggers:** system boot, hibernate/resume, session connect
- **Auto-restart:** every 1 minute on crash, up to 99 attempts
- **Port:** `8765` (firewall rule opened automatically by the installer)
- **Install script:** `install_service.ps1` — must be run **once**, as
  Administrator, from an elevated PowerShell prompt
- **Logs:** `server.log` in the project root (stdout/stderr combined)

See `docs/DEPLOYMENT.md` for the full install/troubleshoot runbook.

## Known Gaps / Things to Watch Before Production

- **No automated tests run in CI** — `tests/` exists (`test_ai_rewriter.py`,
  `test_pipeline_e2e.py`) but nothing triggers them automatically. Consider
  wiring a pre-deploy check.
- **Single point of failure** — this runs on one shop PC. If that PC is off,
  nobody can log entries or generate reports. No redundancy/failover exists
  today. Fine for a single-shift-cadence rollout; revisit if uptime
  requirements tighten.
- **`.env` holds an SMTP password field** (`PASSDOWN_EMAIL_PASS`) that's
  currently unused since Outlook COM automation is the primary email path —
  don't populate it unless you're intentionally switching to SMTP.
- **Unicode logging crash risk** — Windows console encoding (`cp1252`) can't
  print some Unicode characters (e.g. `→`) in `print()` calls; this has
  caused occasional 500 errors on the OT-code endpoint. Worth an audit pass
  for any `print()`/`f-string` using non-ASCII arrows or symbols before launch.

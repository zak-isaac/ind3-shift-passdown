# IND3 Shift Passdown — V3 Command Center

**V3 is the only active web application on this branch.** Starting `python run_server.py` listens on port `8765`. Opening the workshop computer's approved address at `http://HOST:8765/` enters the V3 MST login and Command Center. V2 routes are retired and cannot be opened or used; only the existing report/export engine remains as internal code.

- **Login:** `/v3/login`
- **Workspace:** `/v3`
- **Startup:** `python run_server.py`
- **Master credentials:** configure the approved environment variables described in [V3 deployment instructions](docs/V3_OPERATIONAL_PATCH_DEPLOYMENT.md). Do not store credentials in GitHub.
- **Safety:** stage and test on a backup DB; verify Walmart network/IT approval before allowing other laptops to connect.

**Important:** The V3-only changes are merged into `main`. The workshop PC still needs its local code updated and tested; GitHub changes do not automatically restart or replace its running installation.


## All-shift Cetaris rollout checklist (S1–S5)

V3's import route uses the same shift/date ownership engine for S1, S2, S3, S4 and S5.
Thursday-night S3 continues into Friday morning. Overlap ownership uses confirmed
roster assignments and technician labor, not administrative WO closure.
An imported completed WO that is already credited to another shift is blocked
rather than silently double-counted. Existing tracked breakdown WOs can still
transition to RTS on a later shift.

**Before rollout:** On the workshop PC, update to the reviewed `main` release,
back up the production SQLite DB while the application is stopped, and run:

```powershell
python -m pytest -q tests/test_v3_migration.py tests/test_v3_operational_patch.py tests/test_v3_all_non_pm.py tests/test_v3_split.py tests/test_v3_only.py tests/test_v3_routes.py
python scripts/v3_acceptance.py "C:\\Temp\\trial(1).xlsx" --roster-json "C:\\Temp\\roster.json"
powershell -ExecutionPolicy Bypass -File scripts/verify_windows_v3.ps1 -CetarisFile "C:\\Temp\\trial(1).xlsx"
```

Roster JSON maps Cetaris technician names to their actual home shift, e.g.
`{"Stamps, Ray":"S3"}`. Do not commit real roster data or workbooks to this
repository. The acceptance script produces `v3_acceptance_results.json` locally.
Any ambiguous WO needs MST confirmation before production sign-off.

**Manual acceptance on Windows:** sign in as a rostered MST, upload the real
Cetaris workbook separately to S1–S5 test shifts, confirm only participating
technicians (including OT), resolve ownership review, verify distinct PM counts,
happenings, OOS/RTS transitions, report preview and reimport idempotency.
Check access from an approved second MST laptop via the workshop PC's
approved IP and port 8765. Keep a rollback backup and verify startup after reboot.
The scripts do not replace these authenticated UI and network checks.

---

## Archived implementation reference

The material below documents older components and migration history. V2 screens are not served by the V3-only application.

# IND3 Shift Passdown

Repository initialized. Application source pending private repository setup and review.

# S5 overnight extraction and sidebar acceptance (October 25, 2026)

This is a **development-only** patch until it is reviewed and merged. It does not update the running Workshop application. Back up and reconcile all unpublished Code Puppy work and the facility database before synchronizing `main`.

## S5 ownership expectations

- Friday evening S5 is Friday 17:30 through Saturday 06:00 (plus verified grace to 06:30).
- Saturday evening S5 is Saturday 17:30 through Sunday 06:00.
- Sunday evening S5 is Sunday 17:30 through Monday 06:00.
- A work order completed at Sunday 01:00 normally belongs to Saturday's S5, **not** Sunday's evening S5.
- When the labor start is clearly another shift and a recorded In Service or WO Last Complete time falls after midnight, require MST ownership review. A selected technician may confirm the S5 owner; do not infer ownership from administrative WO Closed Date/Time alone.
- Completed/Closed PM work orders are counted once by WO. Completed non-PM work still needs the explicit include/ignore decision. A work order credited in another shift remains excluded until its prior ownership is resolved.
- Work orders already credited in another shift must no longer block otherwise eligible work orders in the same import. Such conflicts appear in the ignored-WO audit.

## What MST should see

1. Sidebar: actual Indianapolis date and time above **Shift workspace**. The clock is not a simulated/test clock.
2. Upload one Cetaris XLSX covering both calendar days of the full overnight shift; choose correct technicians and any OT.
3. The file preview shows source WOs before shift filtering.
4. In S5, the imported audit shows **after-midnight WOs in file** and **after-midnight included**, so an export issue can be distinguished from ownership/filtering.
5. The ignored audit shows every excluded WO with a reason, including duplicate other-shift credits. Nothing excluded appears in the completed PM count or Shift Happenings.
6. Ambiguous WOs show an MST shift-ownership review; Completed non-PM work offers the existing Add to Passdown / Ignore choices.
7. Shift Happenings, OOS/RTS, AI remarks, preview and EML retain the existing production design. Do not use an experimental dashboard.

## Tests

```powershell
python -m pytest -q tests/test_v3_split.py tests/test_v3_s5_overnight_pipeline.py tests/test_v3_import_audit.py tests/test_v3_routes.py tests/test_v3_ui_consistency.py
```

Also check both GitHub Actions workflows for the current PR head before considering merge.

## Manual validation still needed

- Ask S5 MST for the actual Cetaris XLSX export that produced the empty morning portion, its date range, start date of shift, selected technicians, and the ignored-WO audit. Do **not** commit raw internal work orders or personnel details to a public repository.
- Use a protected, isolated copy of approved test data and a **separate test SQLite database** at home. Do not connect a home machine to the live facility database.
- On the authorized Workshop host, compare local uncommitted templates with this branch before any pull, verify the production database path and host time zone, and test one complete weekend S5 shift after backup.
- If the source file itself contains no next-day technician labor lines, correcting the parser cannot populate missing source records. Expand the Cetaris export date range to include both calendar dates.

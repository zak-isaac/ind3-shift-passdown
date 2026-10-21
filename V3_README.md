# IND3 Shift Passdown — V3 / V2 Function Parity

**Development build. Run in a separate test folder before deploying.**

## What changed

- The V3 entry page uses the visual foundation of the **V2 Walmart homepage**, with associate number + date + shift. **There is no new PIN field.**
- Any associate on the active Drax roster (MST or non-MST) can **start an eligible shift**. V2's time-window and assigned-shift eligibility rules are reused.
- A signed V3 session binds the associate number to exactly one active **shift ID**. An associate can only resume the shift they personally started. Two different shifts can overlap, with the existing two-shift limit.
- The V3 Command Center has the new **single Cetaris Desktop XLSX** upload, post-upload technician selection, closed-work-order filter, PM count, and centered View/Edit hybrid modal.
- The original V2 **maintenance dashboard is reused** at `/v3/operations?shift_id=<id>`. It is not replaced with a partial copy. Its working LOTO, pending PM upload, OOS/RTS, vendor and reliable operations controls, shift notes, carry-overs, report generation, sending/export controls, history, analytics, and end-shift operations are retained.
- When accessed from V3, the old CMMS work-order upload section is replaced by a link back to the **single-file V3 import**. The separate **PMs Needing Completion** upload from V2 is *not* removed.
- The `/` homepage and the existing `/lead`, `/tech`, `/history`, `/analytics`, `/admin`, and report routes are preserved for original V2 users.
- V3 does not introduce an AI writer. **However, the reused original V2 Generate Report handler still invokes the pre-existing V2 rewriting pipeline**, which may use its configured local AI. This is preserved exactly as requested, not newly enabled.

## Important identity and data safety

An associate number **is an identifier, not a secret or strong authentication**. Anybody who knows someone else's number might claim to be that associate. Signed cookies prevent modifying the shift ID in a session, but they do *not* verify who typed the associate number. Until corporate authentication or SSO is integrated, run this only in a restricted, authorized environment, not on an untrusted or internet-facing network.

For production, configure `IND3_V3_SESSION_SECRET` as a securely generated persistent random secret. If unset, a temporary secret is generated for each server process, which logs users out after a restart. V2 legacy session handling remains in place. Do not commit secrets, `.env`, local databases, roster spreadsheets, or actual employee/maintenance records to GitHub.

## Setup

1. Extract into a **separate testing directory**. Do not replace your live IND3 server folder.
2. Install dependencies: `python -m pip install -r requirements.txt`.
3. Set the same approved roster source as the original V2 server (the local `Drax  Associates.xlsx` location, or `ASSOCIATES_PATH`). If the roster is missing, the system cannot identify an eligible associate.
4. Configure the project using the existing V2 configuration/registry; back up any DB before connecting test data.
5. For a stable secure signed session, set `IND3_V3_SESSION_SECRET` in the server environment.
6. Start: `python run_server.py`.
7. Open **`http://localhost:8765/v3/login`** to start or resume. The original **`http://localhost:8765/`** is unchanged.
8. Inside a shift, use **Upload Cetaris File** for WOs and **Maintenance Operations** for all unchanged V2 operational and report controls.

## Core data flow

1. Signed-in shift starter uploads a single Cetaris `.xlsx` file.
2. The backend reads the technician names directly from the file.
3. The user selects technicians and confirms extraction.
4. Only those technicians' labor rows inside the selected shift window are accepted; only `Closed` WOs can enter Shift Happenings or completed PM counts.
5. **Every Closed non-PM work order** becomes a Shift Happening, regardless of Primary RFR (including unrecognized or blank categories). Only PMs are counted separately. The original RFR label is preserved for filtering and review.
6. Multiple technicians on the same WO are consolidated by existing V2 logic, preserving individual remarks; combined Cetaris `Correction` text is not used to attribute someone else's notes.
7. Changes are made through the centered View/Edit modal. Re-import conflicts require review and should never silently discard MST/shift-owner edits.
8. Original V2 report and operations handlers process the same shift database records.

## Testing performed

- V3 isolated routing/import/editor tests, including a real `trial.xlsx` export: passing.
- Integrated V2 main-module smoke test (test-only database): V2 dashboard accessible, V2 PM and other controls present, legacy importer hidden in V3 session, two independent overlapping starter sessions, cross-shift URL and POST checks denied.
- Comprehensive AI/Ollama end-to-end suite was **not** run: the full original suite requires a running local Ollama model and its own test environment.

## Architecture

- `main.py`, `db.py`, `templates/lead.html`: original V2 machinery reused; small additions to validate signed V3 session and route to V3 import.
- `v3_identity.py`: signed associate + shift binding and session validator.
- `v3_routes.py`: V3 login, workspace and operations adapter; single-file XLSX import; review and editing.
- `v3_cetaris.py`: Cetaris XLSX mapping, filtering and grouping.
- `templates/v3/login.html`: V2-style shift entry page with associate number only.
- `templates/v3/workspace.html`, `templates/v3/happening_modal.html`: Design 2 Command Center and hybrid modal.
- `static/v3.css`, `static/v3_design2.css`, `static/v3.js`: Command Center appearance/interactions.

## Deployment note

This build provides functional reuse of V2 controls through the **full original maintenance screen** at `/v3/operations`. It does **not** claim that those advanced controls have been fully visually reimplemented as native widgets in the new Command Center. The goal is to preserve their tested behavior while only changing the work-order import and review path.

## V3 + V2 integration / work order policy

The original V2 routes, handlers, reporting pipeline, and maintenance controls are preserved in this unified development source. V3 reuses them via **Maintenance Operations** (`/v3/operations?shift_id=...`), and uses its own single XLSX import instead of the legacy two-file WO flow. The existing V2 operations screen is not yet fully visually migrated into native V3 panels.

**New rule:** For selected technicians and the shift time window, all **Closed** work orders with any non-PM Primary RFR enter Shift Happenings; Preventive Maintenance is kept out of Happenings and counted as completed PMs. Unknown and blank RFRs are shown with their source label rather than discarded. Re-importing a report that no longer contains an eligible Closed non-PM work order automatically excludes its prior V3-imported entry, preserving the original record and manual edits.

**Testing:** Run `CETARIS_SAMPLE=/path/to/trial.xlsx pytest -q tests/test_v3_single_file.py tests/test_v3_routes.py tests/test_v3_v2_parity.py tests/test_v3_all_non_pm.py`. The V2 AI/Ollama pipeline still requires its own environment for full validation.

## Native V3 maintenance-control update (October 2026)

The V3 Command Center now includes direct, shift-owner-checked forms for OOS asset additions, RTS transitions and log corrections, vendor tracking, and adopting/dismissing previous-shift carry-overs. These no longer require leaving V3 for ordinary use. Vendor names saved in V3 are merged into the original V2 report generator; V3's preview also includes vendors and incoming carry-overs. Existing V2 operations and Outlook/report/export workflows remain available at `/v3/operations`.

The V3 vendor notes are displayed in V3 but only vendor **names** flow to the legacy V2 report because that report's `vendors` field is a list of names. This is not a replacement for the legacy report generator.

Regression tests: `PYTHONPATH=. python -m pytest -q tests/test_v3_native_operations.py tests/test_v3_all_non_pm.py tests/test_v3_routes.py tests/test_v3_v2_parity.py tests/test_v3_identity_ui.py tests/test_wo_consolidator.py`. The full AI test suite still needs its proper `pyspellchecker`, `httpx`, and local Ollama test environment. Production SSO, mail delivery, actual Cetaris export, and full shift lifecycle still need live validation.

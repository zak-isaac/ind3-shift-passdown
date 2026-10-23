# IND3 Shift Passdown V3 — Cetaris Upload Implementation Specification

**Status:** Requirements agreed October 23, 2026. **Implementation and acceptance testing pending.**
**Target:** Existing V3 MST Command Center, using existing V2 maintenance, OOS/RTS, and report UI/behavior.
**Scope:** Complete the single-Cetaris-XLSX import and integrate it with the existing shift workflow. Do not redesign V2, V3, OOS/RTS, report generation, login, or permissions.

> This document records agreed functional requirements. Its presence does **not** mean the code has been implemented or validated. Use synthetic test records in the repository; do not commit operational Cetaris exports, employee rosters, employee identities, databases, or secrets to this public repository.

## Existing architecture to reuse

- `main.py` and `db.py`: existing V2 backend and database, retained.
- `v3_routes.py`: existing V3 upload staging, technician confirmation, processing, manual review, OOS/RTS hooks.
- `v3_cetaris.py`: existing parser and record selection. **Update**, do not replace wholesale.
- `templates/v3/workspace.html`: existing one-file upload dialog, technician selection, and Command Center.
- `associates.py` / current Drax Associates roster: already supplies assigned shift and roster identity. **Do not build a second employee roster.**
- Reuse current OOS and RTS user interface and final report exactly as in V2, including manual editing/override.

## 1. Upload one Cetaris Desktop XLSX

1. MST opens a shift and uploads **one** Cetaris Desktop `.xlsx` export. The export contains rows for technicians across multiple shifts on the day/report.
2. Stage and validate workbook data without immediately creating or overwriting Shift Happenings.
3. Show the existing technician selection dialog after staging. Continue only after MST confirms.
4. Preserve the existing V2 flow; do not require two separate WO files for V3.
5. Explicitly show validation errors (missing columns, bad spreadsheet, empty technician rows). Do not silently discard WOs.

## 2. Existing roster-based technician selection (preserve what already works locally)

1. Use the **existing Drax Associates roster** to preselect technicians assigned to the chosen shift. This behavior may exist in the local Code Puppy workspace even if absent from the GitHub revision: inspect local code before modifying it.
2. Show non-assigned technicians detected in the uploaded file separately, unchecked, so MST can add overtime/split-shift staff.
3. Allow MST to uncheck absentees; only the **confirmed MST selection** controls imported technician remarks.
4. A technician can be selected for more than one shift during legitimate overtime/split-shift coverage.
5. Do not infer assigned shift from the Cetaris `Last Shift` column, and do not create a new roster spreadsheet.
6. Retain selection for the same shift across re-uploads; allow corrections without losing previously imported WOs.

## 3. WO eligibility and attribution (confirmed)

**Normal S1, S2, S3, or a technician assigned to only one weekend shift:**

- Use the uploaded export as the report scope.
- Match `Technician` against MST-confirmed technicians.
- For ordinary non-PM WOs, include `WO Status = Closed`.
- **Exception:** `Primary RFR = Breakdown` qualifies for Shift Happenings when status is `Completed` **or** `Closed`.
- Process `Breakdown / In Process` for OOS tracking, **not** as a completed Shift Happening.
- Do **not** filter out WOs by `Start Date`, WO creation date, labor window, completion time, or WO Closed Date/Time in normal cases. The current `select_records` Start Date time-window restriction needs to be removed for normal shifts.
- An MST may exclude/restore entries manually.

**Specific exception: a technician confirmed to have worked a split between weekend S4 and S5:**

- Determine which of those shifts receives the completed WO using the **same WO's `WO Closed Date/Time`**. Do not use start time, last completion time, or In Service timestamp.
- S4 scheduled window: **05:30–18:00**, Friday–Sunday.
- S5 scheduled window: **17:30–06:00 next day**, Friday–Sunday.
- Example: closed at 15:00 belongs to S4; closed at 20:00 belongs to S5; closed at 02:00 next day belongs to S5.
- 17:30–18:00 is an overlap: flag WO for MST shift-ownership decision.
- If `WO Closed Date/Time` is missing/invalid, including a `Completed` but not `Closed` Breakdown, **flag for MST shift assignment** rather than guessing.
- This special time-attribution logic must **only** activate for a known S4/S5 split-shift technician, not every weekend WO or every ordinary overtime case.
- Use full date+time for S5 crossing midnight.
- Warn when the same WO is already credited to another shift; one completed WO should not be automatically credited as completed in both reports. Permit MST adjudication without erasing legitimate technicians' source remarks.

The recorded WO close time is an **administrative proxy** for shift attribution, not proof of actual technician work time. MST override is required for exceptions.

## 4. Column mapping and output (confirmed)

Use actual Cetaris spreadsheet header names, not column letters.

| Cetaris column | Use |
| --- | --- |
| `WO Number` | Stable work-order identity and deduplication key |
| `Asset #` | **Displayed Asset ID** (specific unit; do not replace with generic `Asset ID` family code) |
| `Service/Repair Description` | **Cause** in Shift Happenings; only this field |
| `Technician` | Filter for confirmed MST-selected staff |
| `Technician Remarks` | Exact original technician work remarks |
| `Primary RFR` | PM vs Breakdown vs other work categories |
| `WO Status` | Closed, Completed, In Process tracking logic |
| `WO Closed Date/Time` | Only for confirmed S4/S5 split-shift attribution |
| `Cause` | **Do not use** as the displayed Cause; MST specifically chose Service/Repair Description only |

**Final Shift Happening layout (retain existing report formatting):**

```text
ASSET ID: <Asset #>
CAUSE: <Service/Repair Description>
TECH REMARKS: <distinct remarks from selected technicians, joined by "; ">
WO#: <WO Number>
```

- One Shift Happening per unique qualifying WO number, not one per technician/labor row.
- If multiple selected technicians worked on the same WO, include each **distinct** `Technician Remarks` value joined literally with `; `.
- Remove exact duplicate remarks; preserve original order as encountered and original spelling, punctuation, and wording. No rewriting, grammar cleanup, hallucinated work, or automatic RTS text.
- Exclude comments from unselected technicians even if the same WO has selected contributors. Retain raw remark provenance internally for auditing/editing.
- Do **not** introduce AI into V3. A simple Copy action can let MST paste text into their preferred external AI; do not add multiple approval steps or a new editor workflow.
- Preserve existing centered View/Edit modal. No separate expandable \"Original Cetaris Record\" section.

## 5. Primary RFR classification and PM count (confirmed)

- Exactly `Primary RFR = Preventive Maintenance` (case/spacing-normalized, allow documented equivalent spelling only if actually present) classifies a PM.
- PM counts require `WO Status = Closed`, selected technician attribution, plus the S4/S5 split rule where applicable.
- **Total Completed PMs = count of distinct qualifying WO numbers for that shift.** Two technicians/rows on the same PM WO count once.
- The Command Center and final report show **only one total completed PM number**. Do not add a PM WO breakdown, list, or expandable PM panel for this purpose.
- Other RFR types (e.g., Proactive Repair, Process Interruption, Found During PM, Work Request, Manager Task, and unknown non-PM RFR values) are ordinary Shift Happenings when Closed.
- `Found During PM` is **not** `Preventive Maintenance` and must not count as a PM.
- `Breakdown` has the separate rules in sections 3 and 7, including the Completed exception.
- Preserve original RFR label on records for internal attribution/review, without changing the final passdown layout.

## 6. Re-upload behavior: Option A (confirmed)

For a later Cetaris XLSX uploaded to the **same shift**:

- **Keep previously imported WOs** when absent from the new upload. Absence is not evidence of completion, reopening, cancellation, or a resolved OOS.
- Match on WO Number for work-order state and breakdown identity; merge distinct selected-technician labor remarks by WO without duplication.
- Add new WOs. Update existing WOs when a newer export contains the **same WO** with changed status or details.
- Preserve MST-entered edits, exclusions and manually confirmed OOS/RTS overrides; never silently overwrite them. Flag conflicting incoming source fields for review.
- Recompute completed PM count from the cumulative unique qualifying WO state for the shift, not the sum of counts from successive uploads.
- Do not duplicate Shift Happenings, PM credit, OOS records, or RTS events on repeated imports.
- If a previously counted WO is explicitly shown with a different/newer non-qualifying status in a later upload, update its current qualifying state after resolving any MST edit conflict. **Do not conflate this with a WO simply missing from the latest file.**
- Cross-shift tracked breakdown updates may be processed when the **same WO number** returns in later imports, including when a different technician finished it. This must **not** bypass the confirmed-technician selection rule for that shift's Shift Happenings.

## 7. Breakdown OOS/RTS tracking: reuse V2 UI (confirmed)

This is a data integration, **not** an OOS/RTS interface redesign.

- Initiate a breakdown-specific record only when `Primary RFR = Breakdown`; key tracking by the **exact WO Number**, with linked `Asset #`.
- `Breakdown / In Process` → track the breakdown as unresolved/OOS in the existing OOS facilities, subject to MST corrections.
- If the **same breakdown WO** later changes to `Completed`, flag it for **MST confirmation** that the equipment is actually operational; only then mark RTS. Preserve \"Keep OOS\" decisions on subsequent uploads.
- If the **same breakdown WO** changes to `Closed`, automatically record RTS **unless** an unresolved other Breakdown WO on that asset or a manual MST OOS override prevents it.
- A Breakdown already Completed or Closed on its **first** upload must be recognized. Closed can auto-RTS under the above safeguards; Completed enters the RTS confirmation workflow and must not be silently treated as confirmed before MST approval.
- A status of `Completed` can mean repairs are complete but the WO remains open for invoicing. Do **not** wait for invoice closure if MST confirms RTS.
- An unrelated PM, Proactive Repair, Process Interruption, or Work Request on the **same asset** must never close or resolve an outstanding Breakdown WO.
- Two distinct Breakdown WOs on the same asset are tracked independently: resolving one cannot resolve the other; asset remains OOS if another unresolved Breakdown WO still requires OOS.
- If a tracked WO is missing from a subsequent export, retain its last known status.
- When multiple `Completed` Breakdown WOs need confirmation, show a **single batched MST confirmation** rather than repeated popups.
- Do not present duplicate confirmation prompts for an already adjudicated unchanged WO.
- Keep original V2 OOS UI, RTS UI, manual controls and final shift-report behavior unchanged.

## 8. Implementation priorities / guardrails for Code Puppy

1. **Inspect the current LOCAL Code Puppy workspace first**, including uncommitted code; it may already preselect roster technicians and show overtime sections beyond the GitHub revision.
2. Back up the local DB and preserve V2 before migrations or import testing. Do not reset a live database or test on production shifts.
3. Make small, reviewable changes in the existing parser/routes/templates/database, adapting what already works.
4. Ensure OOS updates cannot silently override MST decisions; ensure authorization remains tied to the correct shift owner.
5. Reuse existing V2 report, OOS, RTS and operations controls. No new layouts, no AI rewrite, and no separate PM details panel.
6. Do not include actual employee/maintenance data, secrets, or exported rosters in commits.

## 9. Required automated tests / acceptance criteria

Test with synthetic workbooks and a separate temporary database; use real Cetaris exports **locally only** as regression fixtures (never commit them).

1. **Roster:** assigned shift technicians preselected; other-shift techs listed unchecked for overtime; absent staff removable; selections saved.
2. **Filtering:** normal S1/S2/S3 closed WO remains eligible regardless of `Start Date` or WO close time outside scheduled hours.
3. **Non-PM classification:** Closed Proactive Repair, Process Interruption, Work Request, Found During PM, Manager Task and unknown RFR each become a Shift Happening.
4. **PM classification:** only unique Closed Preventive Maintenance WOs counted; duplicate labor rows count once; Found During PM is excluded from PM count; no PM detail list.
5. **Remarks:** two selected techs with distinct remarks on one WO → one happening with exact `; ` joined text; unselected technician's remarks omitted; repeated identical remarks occur once.
6. **Columns:** displayed Asset ID comes from `Asset #`; displayed Cause comes **only** from `Service/Repair Description`.
7. **Split shifts:** weekend S4/S5 split technician closed at 15:00 → S4, at 20:00 → S5, next day 02:00 → S5, 17:45 → MST review; missing close time → MST review. Non-split technicians must never enter this timestamp logic.
8. **Breakdowns:** In Process Breakdown tracks OOS; same WO Completed prompts MST; same WO Closed moves RTS if safe; unrelated WO never resolves it; other unresolved Breakdown blocks asset RTS.
9. **Completed on first import:** Completed Breakdown enters confirmation; Closed Breakdown enters RTS tracking even without prior in-process upload.
10. **Reimport:** same file twice leaves identical totals/records; new file updates same WO status and adds new WOs; missing WOs remain; source changes do not overwrite MST edits.
11. **Parity:** existing V2 OOS/RTS interfaces and report outputs work; V3 route permissions still prevent access to another MST's shift.

**Acceptance gate:** Code Puppy should report which tests passed, which failed, and any assumptions that need MST confirmation, before calling the implementation done.

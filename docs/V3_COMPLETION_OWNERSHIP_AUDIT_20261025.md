# V3 work-order ownership — actual completion, no MST shift-guessing

## Confirmed business rule
V3 shift ownership is determined by the recorded **work completion timestamp
and technician work evidence**, not the administrative WO Closed Date/Time.
An MST selects technicians; there is **no ambiguous work-order owner popup**.
A WO lacking sufficient evidence is skipped individually, with a clear reason
in the imported WO audit. Valid unrelated work orders must continue importing.

## Cetaris fields
- `Technician`, `Start Date`, `Technician Remarks`: original labor and source notes.
- `Completed By` plus `WO Last Complete Date / Time`: reliable completion
  provenance only when Completed By matches a technician actually present in
  that work order's labor rows. Matching accepts reversed first/last order.
- `In Service Date / Time`: supported equipment-return timestamp. Use only
  when consistent with labor start; it must not be earlier than that start.
- `Last Technician`: supports identifying a finisher if `Completed By`
  belongs to an administrative worker rather than the technician.
- `WO Closed Date/Time`: **never shift ownership evidence**. It may represent
  administrative close after maintenance was performed.
- `Last Shift`: historical source context; not sufficient alone for credit.
- `WO Date / Time`: creation time, not repair completion.

## When a WO involves multiple technicians
Group by WO# with original technician notes retained. If the actual completing
technician is uniquely supported, require that technician in the selected
technician set for shift credit. If Cetaris supplies one shared In Service time
without identifying an individual finisher, apply an explicit shift handover
rule only where supported. Do not invent which employee pressed Complete.

## Existing handover rules
- S3 starting Mon–Thu at 20:30 runs through 07:00 next day. Shared S3/S1 or
  S3/S4 work completed before 07:00 belongs to S3 when S3 labor is present.
- S4 Friday–Sunday starts 05:30. S4 takes S4/S5 overlap credit until 18:00.
- S5 starts Friday–Sunday at 17:30, runs through 06:00 next day; the next
  calendar morning retains the prior evening's S5 shift date.
- S5 work attribution from 06:00 to 06:30 requires a confirmed selected S5
  technician; the 07:00 login/report closeout deadline does NOT extend
  ordinary work attribution or reassign another shift's WO.
- No cross-shift double credit; previously credited WOs stay in audit and
  cannot block other imports.

## Actual report discrepancy used for regression
The October export contains a WO whose labor start and equipment In Service
precede the recorded late completion/closure, and whose `Completed By`
person differs from the technician. This must **not** create an S5 repair credit.
The test uses a synthetic equivalent for CI, without publishing the original
employee-identifying record.

## Validated behavior
- No shift-owner dropdown in technician extraction.
- True technician completion + scheduled clock determines owner automatically.
- Work without a verifiable completion signal remains out of Shift Happenings
  and PM count; its audit entry explains why.
- Non-PM Completed-but-not-Closed WO reminder/add/ignore workflow remains.
- Original records are not overwritten when the shift filtering changes.
- Tests: `tests/test_v3_completed_by_ownership.py`,
  `tests/test_v3_split.py`, `tests/test_v3_s5_overnight_pipeline.py`,
  `tests/test_v3_spelling_and_acceptance.py` and the V3 import suites.

## Before deployment
Use an isolated test DB and the date-adjusted Friday S5 XLSX locally.
Compare the ignored-WO audit with the source workbook. Review PR #10,
reconcile unpublished workshop/Code Puppy changes, and merge into main
only after approval. Never test this against the live Workshop shift DB.

# IND3 V3 operational release

This branch promotes V3 shift operations from the experimental implementation.
It intentionally removes developer/test login, sandbox clock, simulated date,
hard-coded test roster, and alternate test dashboard.

## Included
- Normal V3 associate/authorized-management sign-in and real S1–S5 shifts.
- Single-file Cetaris import, WO attribution, technician selection.
- Completed work-order closure decisions and PM/Breakdown handling.
- Import audit of WOs excluded from passdown, without falsely counting them.
- Grouped WO narratives and local Ollama `qwen2.5:3b` opt-in via
  `IND3_OLLAMA_REWRITE=1` and `IND3_OLLAMA_MODEL=qwen2.5:3b`.
- Deterministic source fallbacks, report/EML/PDF generation, live clock.
- Automated regression tests for operational V3 functionality.

## Important
Ollama validation is heuristic and cannot guarantee factual accuracy.
Before use against actual IND3 shifts, confirm the correct local facility
database, source file, recipient configuration and report preview. Automatic
passdown emails can send real messages if enabled; verify recipients first.

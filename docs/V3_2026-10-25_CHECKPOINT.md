# IND3 V3 — October 25, 2026 experimental branch checkpoint

Branch: `feature/v3-ollama-grouped-test` (draft PR #7, stacked on PRs #4–#6).
Changes are **committed to this test branch**, not merged to `main`.

## Functionality in the branch
- V3-only UI startup, redesigned login, manager override, and isolated `T8972` developer workspace.
- Sandbox-local SQLite with independent simulated date/time and shift controls.
- **Live Indianapolis date and clock** displayed in V3 topbar and developer workspace.
  Test mode follows real local date/time by default; a manually selected test clock freezes to that value. The shift workspace labels the simulated clock explicitly.
- Single Cetaris XLSX workflow, group-by-WO handling, PM and completed-work choices, and S1–S5 shift ownership review.
- A single technician-selector UI. S5 morning-grace attribution is derived from selected technicians; ambiguous WOs can still require explicit shift review.
- Sandbox-only roster: Carr/Alexander, Spires/Bradley, Koukpo/Kpedekpo, Robert Jones = S4; Ssaku/Isaac, Melvin/Douglas, Bawi/Mang, Popoola/Waheed = S2; Rob Tice = N/A. Unknown/vendor techs display N/A without automatic preselection.
- Local Ollama `qwen2.5:3b` experimental grouped-remarks rewriting, gated by `IND3_OLLAMA_REWRITE=1`. Original text is the fallback; heuristic validation does **not** guarantee an error-free rewrite.

## Testing and constraints
- Pull updates locally: `git pull origin feature/v3-ollama-grouped-test`, then restart `python run_server.py`.
- Test with `T8972`, switch date and shift, upload an XLSX whose **labor times** fall in the selected window, select technicians once, and confirm import.
- Changing a test clock never retrieves work orders on its own; a compatible Cetaris file is still required.
- Confirm S4/S5 overlap on real exports before deploying the attribution policy to production.
- GitHub Actions is used for regression tests. Check latest CI and exercise the browser path manually prior to merging.

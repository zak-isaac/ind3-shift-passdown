# Workshop V3 Release — October 24–25, 2026

**Branch:** release/workshop-v3-oct25-integration
**Draft PR:** #11 targeting main (no merge has occurred)
**Base source:** review/workshop-v3-integration-20261025
**Existing main:** remains unchanged

All changes below are proposed code only. Do not upload local Cetaris exports,
employee rosters, credentials, production SQLite databases or backups to GitHub.

## Release changes

### Cetaris-based Analytics
The old Down / Degraded / Monitor charts came from legacy manual entry
priorities, not from actual Cetaris work orders, so those metrics are gone.

Analytics now counts source='cmms', excluded=0, grouped by shift ID + WO#:
actual RFR classes (Breakdown, Process Interruption, Proactive Repair, Work
Request), unique non-PM WOs, most-worked assets, repeat-work assets, PM WOs
from pm_wos, WO+PM trends across active and past uploaded shifts, and unique
WO participation for every participating technician. The same WO shared by
two technicians counts as ONE WO but one participation for each technician.
No downtime minutes, asset failure severity, MTTR or other unrecorded metrics
are inferred from a WO status. Implementation is in v3_cetaris_analytics.py,
templates/v3/analytics.html and v3_routes.py.

### Simplified Command Center
- Upload, technician selection, extraction, Replace file and Change technicians
  now appear in one Cetaris card; separate Cetaris report ready panel removed.
- The thousands-row ignored WO review drawer is hidden; backend audit/reasons,
  exclusion and duplicate-shift safeguards remain.
- Shift Happenings has five headers only: Asset ID, Type, Cause, Tech Remarks,
  Action. WO# appears below the asset. Practice-only Completed at and
  Completed badge are removed, and AI Passed/Restored debug labels removed.
- Verified technician completion timestamps are STILL stored and enforced for
  shift attribution across midnight; the user requested removing only their
  extra table column.
- Sidebar contains shift owner, sign-out and Indianapolis time; duplicate
  topbar/header removed. No decorative arrows on V3 login/resume buttons.
- Manager resuming a specific active shift enters that chosen dashboard,
  not a second shift selector.
- Outlook EML now appends a restrained Calibri signoff integrated with the
  report body rather than an oversized bordered signature card. The Spark is
  still an inline CID image for Outlook.

### Preserved October 24–25 operational work
- V3-only production login, shift access, S1–S5 windows and precise S3/S5
  next-day attribution with S3 morning and S4/S5 weekend handover.
- Verified actual technician completion rather than administrative WO closure;
  no ambiguous ownership chooser and no cross-shift duplicate WO credits.
- Multi-technician WO grouping and original remarks; unique PM counting;
  the Completed non-PM Add to Passdown / Ignore reminder workflow persists.
- Breakdown OOS and RTS (confirmed by user): first-seen Closed -> RTS,
  In Process -> OOS, Manager Review Required -> review, manual holds,
  stale exports and manual RTS removals protected.
- Local-only Ollama qwen2.5:3b AI source-protected rewrites, retry, safe fallback,
  tracked original notes, all required technical evidence and assistance notes.
- Ordinary English spelling, grammar and IND3 technical dictionary on BOTH
  Cause and Technician Remarks, shared with report preview and EML.
- Full Code Puppy Shift History, Administration and Analytics page recovery,
  original sidebar and Outlook passdown functions.

## Files needing source-level comparison against actual Workshop versions

- Core: main.py, v3_routes.py, v3_split.py, v3_cetaris.py, v3_identity.py,
  v3_import_audit.py and v3_breakdown.py.
- AI and report: spelling_fix.py, v3_proofread.py, v3_report_layout.py,
  v3_ollama_background.py, signatures.py, current formatter.py.
- Analytics: v3_cetaris_analytics.py (NEW), templates/v3/analytics.html.
- Visuals: templates/v3/workspace.html, _topbar.html, _sidebar.html,
  login.html, history.html, admin.html, templates/partials/v3_assoc_*.html,
  static/v3_design2.css, static/v3.js and static/v3_live_clock.js.
- Tests: tests/test_v3_workshop_release_acceptance.py (NEW), updated
  tests/test_v3_* modules, tests/test_nextgen_signature.py and GitHub workflows.

## Safe Windows Workshop branch test (PowerShell)

First protect uncommitted Code Puppy work with an independent local backup
before any merge or checkout. Review tracked changes and make sure there are
no sensitive files staged. Do not stop or repoint the production server.

    git status --short
    git fetch origin
    git worktree add -b workshop-local-v3-merge-review ..\ind3-v3-merge-review HEAD
    cd ..\ind3-v3-merge-review
    git merge --no-commit --no-ff origin/release/workshop-v3-oct25-integration
    git status
    git diff --name-only --diff-filter=U
    git diff --cached --stat

Any conflicts must be resolved manually with the actual Workshop Code Puppy
files preserved. The merge stays uncommitted and confined to the separate
review worktree; it does NOT replace the Workshop's running source directory.

Use a separate test database and isolated environment to test sign-in,
specific manager resume, Cetaris import, technician selection, after-midnight
WO ownership, mixed PM/happenings, Breakdown OOS/RTS, AI spelling/rewrite,
Analytics correctness, History/Admin and the Outlook EML draft.

    python -m pytest -q tests/test_v3_workshop_release_acceptance.py
    python -m pytest -q tests/test_v3_s5_overnight_pipeline.py
    python -m pytest -q tests/test_v3_operational_patch.py
    python -m pytest -q tests/test_nextgen_signature.py

Local Ollama quality and Outlook visual rendering must still be verified on
the actual Workshop computer. GitHub CI uses mocked AI replies and cannot
prove the installed model will rewrite a particular real WO correctly.

**Release gate:** No merge into main, Workshop deployment, overwrite of
live database, or automatic migration without user review and approval.

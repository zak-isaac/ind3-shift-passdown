# IND3 V3 Workshop integration handoff — October 24–25, 2026

**Working branch:** fix/v3-restore-workshop-sidebar-20261025 (draft PR #10)
**Production main at start of work:** 8a229b710b76bcbbb1c1322f8e1833c0360898ef
**Rollback reference:** archive/main-before-workshop-sync-20261025
**Deployment:** NOT DEPLOYED TO WORKSHOP; keep existing local Workshop versions and databases intact.

The two days are labeled by Indianapolis calendar date; GitHub UTC commits after midnight may show October 26 although the work occurred Sunday evening October 25. The Mac's manually adjusted S4/S5 test clock is not a source for commit dates.

## SATURDAY OCT 24: V3 foundations / operational rules
- Facility S1–S5 shift clocks and eligible starts, including S3 and S5 overnight attribution; realistic S4/S5 handover and S3 before-07:00 shared-work credit. Files: main.py, v3_routes.py, v3_split.py, v3_identity.py, v3_only.py.
- Single Cetaris Desktop XLSX export, technician selection, labor-row grouping by WO, distinct remarks preservation and PM classification. Completed or Closed PMs count once. Completed non-PM remains Add to Passdown / Ignore with Cetaris closure reminder. Files: v3_cetaris.py, v3_routes.py, v3_report_layout.py.
- Breakdown state In Process -> OOS, repaired Completed / Manager Review Required -> RTS decision, verified Closed -> RTS; never invent RTS on unresolved breakdowns or an explicit manual OOS hold. File: v3_breakdown.py.
- V3-only Walmart-inspired Command Center, signed associate login, Shift History, Analytics, Administration and Outlook EML report path. No legacy V2 operational launch and no development-only test clock on the production branch.

## SUNDAY OCT 25: audit, code recovery and fixes

### Code Puppy V3 pages and layout
- Audited all 16 GitHub branches. Correct Code Puppy History, Administration and Analytics were recovered from master and backup-local-before-github-merge rather than using simplified main templates.
- Restored history EML download; analytics graphs and chart data; administration editable associate roster, MST status, facility and Teams settings with management authorization. Recovered partials for roster display and editing.
- Preserved real Command Center sidebar/profile/sign-out, Indianapolis clock, current shift tools, no separate test mode. Do NOT copy historical master or test-mode branches wholesale.
- Relevant files: templates/v3/history.html, templates/v3/admin.html, templates/v3/analytics.html, templates/partials/v3_assoc_list.html, templates/partials/v3_assoc_row.html, templates/partials/v3_assoc_row_edit.html, templates/v3/_sidebar.html, templates/v3/workspace.html, static/v3_design2.css, static/v3.js, v3_routes.py, main.py.

### Verified WO ownership and overnight audit
- Parse Completed By, Last Technician, WO Last Complete, In Service, labor starts, technician identity and RFR. Administrative WO Closed Date/Time is NOT the source of work ownership.
- Attribute WOs automatically to actual participating/completing technicians and the correct shift/date. Removed ambiguous ownership modal; unverifiable WOs are ignored individually with explicit audit reasons. Preserve other included WOs, do not double credit across shifts.
- S5 remains its previous-evening shift date after midnight. Display real verified Completed at time and Next day badge. Persist completion time across subsequent cumulative uploads; ensure after-midnight WOs do not disappear.
- Test example: Oct 25 S5 overnight into Oct 26 around 04:30. Facility time source remains America/Indiana/Indianapolis, regardless of workstation timezone.
- Relevant files: v3_cetaris.py, v3_split.py, v3_routes.py, templates/v3/workspace.html, static/v3_design2.css, v3_identity.py, main.py.

### Breakdown lifecycle correction (confirmed working locally)
- Fix first-seen verified Closed Breakdown WOs so they appear under RTS even if no previous In Process/OOS record was ever imported.
- Repair older same-shift Closed Breakdowns incorrectly marked processed without RTS event when the same Excel file is reimported.
- Preserve distinct rules for In Process OOS, Manager Review Required/Completed decisions, manual OOS holds, manual RTS removal, ongoing other breakdowns, duplicate/stale export handling.
- Relevant files: v3_breakdown.py, v3_routes.py; tests/test_v3_operational_patch.py, tests/test_v3_s5_overnight_pipeline.py. Do not roll back working local Breakdown behavior.

### AI and spelling, Cause AND Remarks
- Ollama local-only qwen2.5:3b structured JSON work log; preserve original Cetaris source. Protect from prompt echo, unsupported work, lost measurements/parts, omitted collaborator remarks and fake RTS claims.
- Original overstrict verifier caused excess Original Restored: novel English words were rejected as inventions and 75% word overlap rejected correct paraphrases. Now check factual actions, components, numbers, distinct meaningful notes and documented outcomes rather than blanket word overlap.
- A second tech's "Assist" note is preserved ("Assisted.") without inventing repairs.
- Retry AI can rerun failed/legacy unsafe drafts; queue recovers from model stalls and reports AI Queued / Processing / Passed / Original Restored with reasons.
- Automatic offline English dictionary typo correction runs on BOTH Cause / Issue and Technician Remarks (including model paragraphs), protects WO/asset IDs, abbreviations and technical jargon. Clear known examples: griper/griping, boixes, toren, throught, produck, servor and industrial paper-wrapping context. Original raw remarks unchanged.
- Relevant: spelling_fix.py, v3_proofread.py, v3_report_layout.py, v3_ollama_background.py, templates/v3/workspace.html, static/v3_design2.css.
- Relevant tests: tests/test_v3_english_proofread_report.py, tests/test_v3_ollama_background.py, tests/test_v3_full_workflow.py, tests/test_v3_spelling_and_acceptance.py.

### Reports
- Unify Asset: Cause heading and source-faithful Technician Remarks bullet for Shift Happenings across workspace, preview, EML, Word and PDF. Keep multi-technician actions; no synthetic repair claims.
- Keep accurate audit totals, exclusions/ignored reasons, PM counts, shift-identity and after-midnight work details; repeated imports must not manufacture shift credits.

## Integration files (check related routes, templates and tests together)
- Core shifts/session: main.py, v3_routes.py, v3_identity.py, v3_only.py, v3_split.py.
- Cetaris/evidence: v3_cetaris.py, v3_split.py, v3_routes.py, v3_report_layout.py.
- English/AI: spelling_fix.py, v3_proofread.py, v3_ollama_background.py, v3_report_layout.py.
- Breakdown: v3_breakdown.py, v3_routes.py.
- UI: templates/v3/workspace.html, _sidebar.html, _topbar.html, history.html, admin.html, analytics.html; templates/partials/v3_assoc_*.html; static/v3_design2.css; static/v3.js.
- Tests: tests/test_v3_*.py, .github/workflows/v3-tests.yml, .github/workflows/v3_spelling_review.yml.

## Workshop review branch procedure (PowerShell; isolated, not live)
1. Stop no production service. Inspect local unpublished Code Puppy changes and back up the production DB, registry, settings and relevant code separately. NEVER commit DBs, real Cetaris exports, Drax roster, .env, secrets or production backups.
2. From the clean workshop Git checkout: git status --short; git fetch origin.
3. Create a separate worktree from current Workshop HEAD so the running server folder is unaffected:
   git worktree add -b workshop-v3-review-20261025 ..\ind3-v3-review HEAD
4. In the NEW review directory only:
   cd ..\ind3-v3-review
   git merge --no-commit --no-ff origin/fix/v3-restore-workshop-sidebar-20261025
5. Review conflicts manually, preserving newer Workshop templates/features over stale historical ones. Check git diff --stat and git status before committing anything. This is a working-tree merge for review, NOT a main merge.
6. Use an ISOLATED test DB and approved networking to run tests: S1–S5 login; Excel upload; tech selection; grouped WOs; PM counts; OOS/RTS; S3/S5 midnight ownership; Cause + Remarks spelling; AI valid/unsafe fallback; Completed at time; History, Analytics, Admin and EML.
7. In GitHub Actions verify both current workflows are green. AI model tests use mocks; they do not prove that the actual installed Workshop model produces good output. Validate real Ollama locally.
8. Require explicit acceptance before merging to main or replacing Workshop live files.

**Never blanket-merge master, historical test mode, old ownership-popup branches or production database files.**

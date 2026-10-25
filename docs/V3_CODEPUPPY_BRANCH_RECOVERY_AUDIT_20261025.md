# IND3 V3 — GitHub branch-by-branch Code Puppy recovery audit
**Audit date:** October 25, 2026. **Working branch:** `fix/v3-restore-workshop-sidebar-20261025` (draft PR #10).
**Production main before this recovery:** `8a229b710b76bcbbb1c1322f8e1833c0360898ef`.

## Primary finding
The V3 History, Administration and Analytics templates on `main` and PR #10
were simplified/incomplete compared with the recovered originals on `master`,
`backup-local-before-github-merge`, `feature/v3-s1-s5-production-corrections`,
and `fix/local-workshop-test-mode`. The correct History page has a
past-report Outlook EML download; Analytics has Charts and executive trends;
Administration has a complete editable facility roster, settings, and Teams
notification form. Those richer versions share identical content hashes
across several historical branches, confirming the intended design lineage.

## Branch inventory (all 16 existing branches checked)
| Branch | Snapshot role | Recovery decision |
|---|---|---|
| `main` | Baseline merged PR #1, #3, #4, #8, #9 | Preserve as production baseline until explicit merge |
| `master` | Original expanded Command Center, History, Administration, Analytics, roster table partials, CSS/JS | Source of verified page designs and missing route behavior, **not** wholesale merge |
| `backup-local-before-github-merge` | Local prereconciliation snapshot; matches expanded three V3 page templates | Backup cross-check |
| `feature/v3-s1-s5-production-corrections` | Expanded three pages matching master; shift fixes | Design provenance; compare shift rules before porting |
| `fix/local-workshop-test-mode` | Expanded pages and a differently developed sidebar, local test-mode work | Design provenance only. **Do not restore test-mode runtime** |
| `feature/v3-operational-integration` | Earlier functional V3 integration, merged PR #1 | Superseded by current V3 |
| `feature/v3-s4-s5-attribution-grace` | Shift attribution/grace work, merged PR #3 | Superseded by current verified technician completion rules |
| `feature/v3-completion-ownership-modal` | Old ambiguous ownership popups, PR #2 unmerged | **Do not restore.** Removed by user decision |
| `v3-spelling-and-acceptance-20261025` | Earlier spelling/completion safeguards, PR #4 merged | Already represented by newer V3 |
| `feature/v3-deterministic-remark-writer` | Experimental alternative remark writer, PR #5 unmerged | Keep experimental; do not replace user-approved Ollama + fallback |
| `fix/v3-login-dev-management` | Early test login/management integration, PR #6 unmerged | Do not reintroduce dev/test login |
| `feature/v3-ollama-grouped-test` | Early grouped AI, PR #7 unmerged | Production includes advanced Ollama features via PR #8 |
| `release/v3-operational-ai-20261025` | Operational AI production promotion, PR #8 merged | Present in main, enhanced on PR #10 |
| `fix/v3-e2e-ui-ai-eml-20261025` | E2E AI/UI/EML, PR #9 merged | Present in main, enhanced on PR #10 |
| `archive/main-before-workshop-sync-20261025` | Identical pre-recovery production main snapshot | Explicit rollback reference; don't alter |
| `fix/v3-restore-workshop-sidebar-20261025` | Current draft PR #10 and targeted recovery integration | Preserve current WO attribution, AI fallback, S3/S5 clock, original dashboard, and restored pages |

## Recovered matching files
- `templates/v3/history.html`: restored Code Puppy detailed shift records + report EML link.
- `templates/v3/admin.html`: restored roster management, Teams notification, and facility/email settings.
- `templates/v3/analytics.html`: restored Chart.js charts, trend panels, repeat-asset and technician insights; JSON script data uses safe Jinja serialization.
- `templates/partials/v3_assoc_list.html`, `v3_assoc_row.html`, `v3_assoc_row_edit.html`: added missing actual Code Puppy HTMX roster widgets.
- `static/v3_design2.css`: selectively reuses Code Puppy V3 admin CSS; **does not overwrite newer** audit/import/side-clock styling.
- `v3_routes.py`: restored EML export and signed-session-checked roster CRUD, manager authorization, Teams settings.
- `main.py`: reuses the existing historical Outlook composer and facility roster DB refresh function.
- `templates/v3/_sidebar.html`: intact profile, sign-out and live Indianapolis clock above Workspace, with full-page navigation so restored pages' chart scripts run.
- `templates/v3/workspace.html` and `v3_routes.py`: add recorded technician-supported WO completion timestamp with "Next day" marker for after-midnight S3/S5 work, **not** administrative WO closure time.

## AI Passed diagnostic
`v3_ollama_background.enabled()` is opt-in and returns true only when
`IND3_OLLAMA_REWRITE=1` is configured for the local server.
A passed suggestion is never fabricated: draft must come from localhost Ollama
(qwen2.5:3b by default), pass technical token, numeric, multi-technician note
and unsupported-word checks, and match current source hash. If it fails, the
original technician notes are used. No MST approval step is needed.
The V3 dashboard now identifies AI Disabled and exposes fallback reasons
as the work-order AI status tooltip. If the user enabled the model *after*
the original import, a fresh test import into an isolated DB is the clearest
verification of worker invocation. Check `ollama list` and the local .env;
do **not** mark invalid drafts as AI Passed merely to make the label appear.

## Safety and deferred merges
Several historical branches, especially `master`, contain committed legacy
local artifacts, debug outputs, backup DB files and code-generation experiments.
A wholesale merge could overwrite the active architecture or inadvertently
publish historical operational data. None of those files are copied here.
All local test files and the Workshop production SQLite DB remain unchanged.
This recovery is source-only on the draft PR, not a production deployment.
Review fresh GitHub CI and validate page behavior on an isolated local DB
before any explicit merge into `main`.

# Changelog

Dated entries, most recent first. Written so anyone picking this up — not
just Isaac — can see what changed, why, and what's actually live.

## 2026-10-26 — Production fully switched to Workshop V3; same-day follow-up fixes

**Deployed and live:** `production-live` branch, commit `ef049df`, running on
**port 8965**. Database: `IND3-Passdown-Server\dbs\ind3.db` (backup at
`dbs\ind3.db.bak_before_v3_deploy_20261026`). Two real active shifts (S2 #127,
S3 #128) survived every restart today untouched.

### Major milestone: production switched to Workshop `main`
The former production branch and Workshop's `main` had **zero shared git
history**, so a normal merge was impossible. Production was fully cut over to
Workshop's codebase via a new `production-live` branch created directly from
Workshop `main`. Full suite (391 tests at the time) passed in the actual
production virtualenv (`fastapi==0.136.1`) before the switch.

Deliberately **not** ported from the personal sandbox branches:
- `is_test_shift` sandbox feature (dropped entirely).
- Sandbox `assign_shift()` ownership override — Workshop's `v3_split.assign()`
  explicitly rejects administrative ownership overrides by design. Do not
  re-add this without a real discussion; it was excluded on purpose.
- The sandbox's `WO Closed Date/Time` / `Update Date/Time` heuristic for
  ownership — Workshop uses verified completion evidence instead.

### Feature work ported from Isaac's personal GitHub branches (same day)
Three separate asks today all turned out to be things already built on
`feature/v3-s1-s5-production-corrections` or similar personal branches, just
never merged into Workshop's `main`, or built and then later simplified away.
Lesson for next time: `git log --all -S"<feature text>"` across every branch
**before** building something from scratch — it's usually already there.

1. **"Needs Review" panel removed, "Imported WO audit" detail restored.**
   The actionable Include/Dismiss panel (added earlier the same day) was
   removed from the UI per feedback — backend (`v3_import_review.py`) is left
   in place but unused, in case it's wanted again. In its place, restored a
   **read-only** detailed audit drawer (KPI counts + collapsible ignored-WO
   table with reasons) that had briefly existed on commit `9ec78d0` before a
   later same-night simplification (`5f835da`) replaced it with a one-line
   summary. Several tests had locked in that old simplified state and needed
   updating to lock in the new one instead.

2. **"PM's Needing Completion" file upload**, replacing the old read-only "PM
   Summary" count. This is a genuinely different Cetaris report than the main
   completed-WO import: an **open/pending PM export** ("dailies to be done" —
   WO Number, WO Date, PM Due Date, Promise Date, Asset #). Ported from
   personal-branch commit `7b13bd6`. Backend reuses V2's already-proven,
   untouched infrastructure directly (`pm_pending_parser.py`,
   `db.save_pm_file`/`get_pm_files`/`delete_pm_file`,
   `db.save_pending_pms`/`get_pending_pms`/`clear_pending_pms`, the shared
   `pm_uploads/` directory). New V3-native routes render through a proper
   Jinja partial (`templates/partials/v3_pm_section.html`) instead of V2's
   old Tailwind-string-building approach.

3. **CSS cleanup + visual polish.** Found and removed **three overlapping
   generations** of `.d2-wo-table` column-width CSS rules from different
   redesign passes (6-column, 7-column, and the correct 5-column one) that
   were silently fighting each other via specificity/source-order luck.
   Discovered Shift Happenings table rows had **zero row-level CSS** anywhere
   (no border, no zebra stripe, no hover) and that `.d2-table-scroll` had no
   CSS at all, not even `overflow-x`. Fixed both, reusing the already-proven
   `.v3-roster-table` pattern so Happenings and the Audit drawer now look
   like one app instead of two unrelated feature drops. Also fixed the new
   PM-upload file-list links, which were unstyled `<a>` tags with no visual
   affordance of being clickable.

### Confirmed, not changed: associate 2759's privileges
Isaac's personal associate number (`2759`) already has full
manager/emergency-override rights via the `_MASTER_CODES` mechanism in
`main.py`, granted **at the session level only** when logging in with that
number — not via the `is_manager` flag on the roster record itself (which is
correctly `0` for 2759 in production, same as `is_mst`). This was already
correct in `main.py` / `v3_identity.py` / `v3_routes.py`; it just had no test
coverage, so a regression test was added
(`test_personal_override_2759_gets_elevated_rights_without_manager_flag` in
`tests/test_v3_v2_parity.py`). **Nothing about this behavior should be
changed without updating that test.**

### State at end of day
- Full suite: **395 passed, 5 skipped, 0 failed**, in both the dev venv and
  the real production venv.
- `main`, `integration/workshop-v3-oct25-merge`, and `production-live` are
  all at `ef049df` and fully pushed to `github/*`.
- Live server PID changes every restart (expected) — check with
  `netstat -ano | findstr ":8965"` before touching it, and always preserve
  `dbs/ind3.db` across restarts (never delete/recreate it).
- To redeploy after further Workshop changes: fast-forward `production-live`
  to the tested commit, run the full suite in the **production venv**
  specifically (fastapi version differs from the dev venv), restart uvicorn
  on port 8965, then verify `curl http://localhost:8965/` returns `303` to
  `/v3` and that active shifts in `dbs/ind3.db` are unchanged.

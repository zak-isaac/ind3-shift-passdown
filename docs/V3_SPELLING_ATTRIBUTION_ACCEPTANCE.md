# IND3 V3 — Spelling review and WO attribution acceptance

This is a **review branch**, not a workshop deployment. Code Puppy must compare any newer local work before merge. Back up the IND3 application and SQLite database before staging this patch.

## Changes under review

1. **Spelling:** In editable Shift Happenings, manual entries, notes, and vendor notes, choose **Check spelling** to preview deterministic corrections. Choose **Apply suggestion** or **Keep original**. Applying fills the editor only; **Save Changes** is still required. The original Cetaris export remains unchanged. This is intentionally conservative spelling/capitalization/punctuation, **not creative rewriting or full sentence grammar**. Protect acronyms, numbers, equipment names, and WO identifiers. Confirm corrections before accepting.
2. **Shift attribution:** S3 can retain a shared S1/S4 WO when recorded equipment return or last completion evidence is before 07:00 on the S3 overnight shift. If completion is after 07:00, the later shift may own it, with normal evidence checks. S4 starts that finish after its 18:00 boundary can be attributed to S5. The S5 06:00–06:30 extension still requires technician confirmation. Work without enough evidence goes to manual ownership review. WO close time is **not** the work-completion time.
3. **Completed / not Closed:** After selecting technicians, a Cetaris **Completed** WO requires MST review with exactly **Add to Passdown and remind technicians to close the WO before shift end** or **Ignore**. Adding creates an explicit passdown reminder, **not a completed PM**. No reminders are inserted into the source Excel file; Closed PMs alone contribute to the PM count. The chosen status is displayed as **Completed · Close WO**, not Closed.
4. **Attribution audit:** The local SQLite `v3_wo_attribution_audit` table records unique WO/shift decisions and supporting timestamps and roster/confirmation evidence, without copying the remark text again. The `v3_pending_closure` table records MST closure-review decisions and is cleared when a later export confirms Closed status.
5. **LAN access hardening:** By default the launcher now binds to `127.0.0.1`. The workshop-PC operator can choose an approved LAN bind by setting `IND3_V3_BIND_HOST` and `IND3_V3_ENABLE_LAN=1` together with a persistent `IND3_V3_SESSION_SECRET` of at least 32 bytes. For an IT-approved HTTPS deployment, `IND3_V3_SECURE_COOKIES=1` sets Secure cookies. Do not enable it on ordinary HTTP or login cookies will not be transmitted. Associate-number-only login is identification, **not sufficient proof of identity**; IT-approved network restrictions or SSO remain necessary.

## Acceptance on isolated Windows staging

Run tests with the reviewed branch and staging DB; do not point tests at production:

```powershell
python -m pytest -q tests/test_v3_spelling_and_acceptance.py tests/test_v3_split.py tests/test_v3_operational_patch.py tests/test_v3_routes.py tests/test_v3_migration.py tests/test_v3_only.py
python scripts/v3_acceptance.py "C:\\Temp\\trial(1).xlsx" --roster-json "C:\\Temp\\roster.json"
powershell -ExecutionPolicy Bypass -File scripts/verify_windows_v3.ps1 -CetarisFile "C:\\Temp\\trial(1).xlsx"
```

Then test the authenticated V3 UI with a real, approved Cetaris workbook: S3 running Thursday to Friday, S3/S4 shared WOs completed just before and after 07:00; S4/S5 WOs completed just before/after 18:00 and at midnight; missing timestamp reviews; returning a previously OOS asset to service; re-importing the identical workbook; editing remarks and checking spelling; verifying no duplicate credit and no PM count for unclosed WOs. Check generated PDF/Word/Outlook output, signed-login behavior, report end-shift lifecycle, approved second-laptop access, and restart/rollback.

**Production gate:** retain the current working deployment unless this checklist, existing suite, real-file review, and Code Puppy comparison all pass.

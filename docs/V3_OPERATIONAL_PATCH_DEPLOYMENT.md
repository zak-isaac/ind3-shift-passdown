# IND3 V3 operational patch — staging and deployment

These **application source code changes** are merged into `main`, not merely specified. The workshop production application must NOT be replaced until local Code Puppy changes are reconciled and all acceptance checks pass.

## V3-only startup and retired V2

The standard `python run_server.py` startup now exposes **V3 only**:
- `http://<approved-workshop-host>:8765/` redirects to `/v3`; unauthenticated MSTs see `/v3/login`
- V2 home, lead, tech, admin, shift mutation and other legacy routes are unregistered, not just hidden
- Existing `/report/{id}/compose-outlook`, PDF, Word, EML and text exports remain available **only to authorized V3 sessions**
- V3 login issues only the signed V3 cookie and clears stale V2 cookies
- The existing database, reporting engine and exporters remain in place; no V2 screens are exposed

This is a **route retirement**, not destructive removal of the historical source or stored shifts. Do not delete the V2 database or the shared report-generation code.

## Changes in this branch
- Cetaris: no Start Date eligibility filtering for normal shifts; WO Closed Date/Time parsed; Completed Breakdown becomes a happening; In Process Breakdown enters WO-specific OOS tracking; PM counted only when Preventive Maintenance + Completed or Closed, with a unique WO counted once.
- Drax: shift-assigned technicians prechecked on the existing selection dialog; other staff optional.
- S4/S5: MST can mark split-shift technicians and adjudicate overlap/missing WO close timestamps. Check duplicate WO credit across shifts before production release.
- Re-import: WOs missing from subsequent exports retained, explicit reopened WOs excluded; previous manual edits flagged.
- Breakdown: persistent exact-WO status, Completed batch RTS/Keep OOS, Closed auto RTS only if no other unresolved Breakdown or manual OOS hold, and idempotent RTS logs.
- Master: shared and personal master credentials must be supplied through **server environment variables**, not GitHub or a hardcoded bypass. Ordinary associate number remains identification, not proof of identity.
- V2: routes and report generation remain intact; previously committed Outlook signature code unchanged.

## Required environment configuration
Use the Windows service/task account's protected environment, **not** a tracked file:
- `IND3_V3_SESSION_SECRET`: persistent cryptographically random secret (32+ bytes)
- `IND3_V3_MASTER_CODE`: approved shared code (configure on server; do not commit)
- `IND3_V3_PERSONAL_MASTER_ASSOC`: explicitly approved personal master associate number
- `IND3_V3_PERSONAL_MASTER_SECRET`: separate strong personal credential
- `ASSOCIATES_PATH`: actual approved local path to Drax Associates workbook

A four-digit shared code and associate-number-only ordinary login are **not sufficient production authentication** for broad network exposure. Obtain Walmart IT approval and use SSO or approved access controls before allowing access from other laptops. Do not expose port 8765 to the internet.

## Validation gate
1. Compare branch against Code Puppy's latest **local** workspace. Preserve uncommitted fixes.
2. Back up database, application and roster; stage with synthetic data and separate DB.
3. Run `PYTHONPATH=. python -m pytest -q tests/test_v3_operational_patch.py tests/test_v3_split.py tests/test_v3_migration.py tests/test_v3_routes.py tests/test_v3_v2_parity.py`.
4. Test real Cetaris upload in authorized isolated staging only; verify technician selection, cumulative reimports, shared WO remarks, PM count, Breakdown OOS/RTS, split decisions, permissions, full V2 report and signature in Outlook.
5. Confirm full report's joined technician remarks and WO attribution match MST's approved layout.
6. Test server boot without login, approved workshop IP/hostname:8765, restricted firewall, laptop access, restart recovery, and rollback.
7. Do not merge/deploy until every required acceptance check is PASS or explicitly blocked with a remediation plan.

**Rollback:** stop staged service, restore backed-up code/DB, restart original V2/V3 service. Never test migrations on production data without a restorable backup.

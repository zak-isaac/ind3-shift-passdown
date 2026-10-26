"""Visible WO-level audit for a confirmed Cetaris import.

This is diagnostic only: ignored WOs never enter Shift Happenings or PMs.
Each row represents one source WO, regardless of labor-line duplication.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from v3_split import _actual_completion


def preview_status(rows):
    by_wo = defaultdict(set)
    techs = set()
    for row in rows:
        wo = str(row.get("wo_number") or "").strip()
        if not wo:
            continue
        by_wo[wo].add(str(row.get("status") or "").strip().casefold())
        if row.get("tech_name"):
            techs.add(row["tech_name"])
    summary = {"work_orders": len(by_wo), "closed": 0, "completed": 0,
               "other": 0, "technicians": len(techs)}
    for statuses in by_wo.values():
        if "closed" in statuses:
            summary["closed"] += 1
        elif "completed" in statuses:
            summary["completed"] += 1
        else:
            summary["other"] += 1
    return summary


def build_audit(rows, selected, attributed, records, conflicting_wos=None,
                shift_type=None, shift_day=None, unresolved_wos=None):
    """Preserve ignored WOs and explain why they do not count for this shift."""
    by_wo = defaultdict(list)
    selected = set(selected)
    conflicting_wos = set(conflicting_wos or ())
    ownership_reasons = {str(item.get('wo_number')): item.get('reason', '')
                         for item in (unresolved_wos or [])}
    for row in rows:
        wo = str(row.get("wo_number") or "").strip()
        if wo:
            by_wo[wo].append(row)
    kept = defaultdict(set)
    for r in records:
        kept[str(r.get("wo_number") or "")].add(r.get("kind"))
    attributed_wos = {str(r.get("wo_number") or "") for r in attributed}
    ignored = []
    oos_only = 0
    for wo, items in by_wo.items():
        if wo in kept:
            if kept[wo] == {"oos"}:
                oos_only += 1
            continue
        selected_lines = [r for r in items if r.get("tech_name") in selected]
        selected_statuses = {str(r.get("status") or "").strip().casefold()
                             for r in selected_lines}
        if wo in conflicting_wos:
            why = "Already credited to another shift (not counted)"
        elif not selected_lines:
            why = "Technician not selected (other shift or vendor)"
        elif not selected_statuses.intersection({"closed", "completed"}):
            why = "Not Closed or Completed"
        elif wo in ownership_reasons:
            why = "No automatic ownership: " + ownership_reasons[wo]
        elif wo not in attributed_wos:
            why = "Outside this shift or missing verified technician completion evidence"
        elif "completed" in selected_statuses and "closed" not in selected_statuses:
            why = "Completed but not Closed — not included"
        else:
            why = "Not eligible for this passdown"
        sample = selected_lines[0] if selected_lines else items[0]
        # Only genuinely evidence-ambiguous reasons are offered for manual
        # MST inclusion (see v3_import_review.py) -- never a WO that's
        # already credited elsewhere, not from a selected technician, or
        # not yet Closed/Completed. Those are clear-cut exclusions, not
        # judgment calls, and must never be silently overridden.
        includable = why.startswith("No automatic ownership") or why.startswith("Outside this shift")
        ignored.append({
            "wo_number":wo, "asset_id":sample.get("asset_id") or "",
            "status":", ".join(sorted({str(r.get("status") or "Unknown") for r in items})),
            "technicians":", ".join(sorted({str(r.get("tech_name") or "") for r in items})),
            "start_at":sample.get("start_at") or "",
            # Display-only provenance, never a decision input (2026-10-26):
            # v3_split.py's completion-evidence engine deliberately never
            # consults WO Closed Date/Time OR Update Date/Time for ownership
            # -- but showing the real last-touched time gives a human
            # reviewer honest context an administrative close date can lack.
            "updated_at":sample.get("updated_at") or "",
            "reason":why, "includable":includable, "raw_row":sample,
        })
    ignored.sort(key=lambda r:(r["start_at"],r["wo_number"]),reverse=True)
    # Sunday-morning labor still belongs to Saturday S5. Show distinct
    # actual-work WOs both present and included, not administrative close dates.
    after_midnight_source = set()
    after_midnight_included = set()
    if shift_type == "S5" and shift_day:
        following = date.fromisoformat(str(shift_day)) + timedelta(days=1)
        included_wos = {wo for wo, kinds in kept.items()
                        if kinds.intersection({"pm", "happening"})}
        # A WO Last Complete value may represent delayed administration,
        # not a technician's actual after-midnight work. Only count real labor
        # start or verified completion/in-service signals.
        for wo, labor in by_wo.items():
            completion = _actual_completion(labor)
            evidences = [datetime.fromisoformat(str(r["start_at"]))
                         for r in labor if r.get("start_at")]
            if completion:
                evidences.append(completion[0])
            if any(when.date() == following and
                   (when.hour, when.minute) <= (6, 30) for when in evidences):
                after_midnight_source.add(wo)
                if wo in included_wos:
                    after_midnight_included.add(wo)
    return {**preview_status(rows), "source_rows":len(rows),
            "after_midnight_source_wos":len(after_midnight_source),
            "after_midnight_included_wos":len(after_midnight_included),
            "selected_technicians":len(selected),
            "included_work_orders":len(kept)-oos_only,
            "oos_work_orders":oos_only,
            "ignored_count":len(ignored), "ignored":ignored}

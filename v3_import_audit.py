"""Visible WO-level audit for a confirmed Cetaris import.

This is diagnostic only: ignored WOs never enter Shift Happenings or PMs.
Each row represents one source WO, regardless of labor-line duplication.
"""
from collections import defaultdict


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


def build_audit(rows, selected, attributed, records):
    """Preserve ignored WOs and explain why they do not count for this shift."""
    by_wo = defaultdict(list)
    selected = set(selected)
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
        if not selected_lines:
            why = "Technician not selected (other shift or vendor)"
        elif not selected_statuses.intersection({"closed", "completed"}):
            why = "Not Closed or Completed"
        elif wo not in attributed_wos:
            why = "Outside this shift or missing shift-time evidence"
        elif "completed" in selected_statuses and "closed" not in selected_statuses:
            why = "Completed but not Closed — not included"
        else:
            why = "Not eligible for this passdown"
        sample = selected_lines[0] if selected_lines else items[0]
        ignored.append({
            "wo_number":wo, "asset_id":sample.get("asset_id") or "",
            "status":", ".join(sorted({str(r.get("status") or "Unknown") for r in items})),
            "technicians":", ".join(sorted({str(r.get("tech_name") or "") for r in items})),
            "start_at":sample.get("start_at") or "",
            "reason":why
        })
    ignored.sort(key=lambda r:(r["start_at"],r["wo_number"]),reverse=True)
    return {**preview_status(rows), "source_rows":len(rows),
            "selected_technicians":len(selected),
            "included_work_orders":len(kept)-oos_only,
            "oos_work_orders":oos_only,
            "ignored_count":len(ignored), "ignored":ignored}

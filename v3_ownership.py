"""V3 completion ownership planner. Never assign work credit by administrative close time."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime

PM = {"preventive maintenance", "preventative maintenance", "pm"}
FINISHED = {"completed", "closed"}

def as_dt(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    try:
        return datetime.fromisoformat(str(value)) if value else None
    except (TypeError, ValueError):
        return None

def normalized(value):
    return " ".join(str(value or "").casefold().split())

def plan_work_orders(rows, selected, shift_code, start, end, roster_shifts=None, saved=None):
    """One decision per WO, retaining all labor remarks; unknown ownership is not guessed."""
    selected_keys = {normalized(t) for t in selected}
    roster_shifts = {normalized(k): str(v).upper() for k, v in (roster_shifts or {}).items()}
    saved = saved or {}
    by_wo = defaultdict(list)
    for r in rows:
        if str(r.get("wo_number") or "").strip():
            by_wo[str(r["wo_number"]).strip()].append(r)
    results = []
    for wo, group in by_wo.items():
        participating = [r for r in group if normalized(r.get("tech_name")) in selected_keys]
        if not participating:
            continue
        statuses = {normalized(r.get("status")) for r in group}
        status = "closed" if "closed" in statuses else ("completed" if "completed" in statuses else "in process")
        category = normalized(next((r.get("rfr") for r in group if r.get("rfr")), ""))
        kind = "pm" if category in PM else "happening"
        completion = [as_dt(r.get("last_complete_at")) for r in group]
        completion = max((v for v in completion if v is not None), default=None)
        techs = list(dict.fromkeys(str(r.get("tech_name") or "").strip() for r in group if r.get("tech_name")))
        known_home = {roster_shifts.get(normalized(t)) for t in techs} - {None}
        selected_home = {roster_shifts.get(normalized(r.get("tech_name"))) for r in participating} - {None}
        cross_shift = len(known_home) > 1
        outside = completion is not None and not (start <= completion <= end)
        foreign = bool(selected_home and shift_code not in selected_home)
        ambiguous = cross_shift or outside or foreign or completion is None
        stored = saved.get(wo) or {}
        owner = stored.get("owner_shift")
        decision = stored.get("decision")
        if status not in FINISHED:
            action = "oos" if category == "breakdown" else "skip"
        elif decision == "include" and owner == shift_code:
            action = "auto"
        elif decision in {"exclude", "defer"}:
            action = "pending" if decision == "defer" else "exclude"
        elif owner and owner != shift_code:
            action = "other_shift"
        elif ambiguous:
            action = "ownership"
        elif status == "completed" and kind != "pm":
            action = "verification"
        else:
            action = "auto"
        remarks = list(dict.fromkeys(str(r.get("remarks") or "").strip() for r in group if r.get("remarks")))
        results.append({
            "wo_number": wo, "asset_id": next((r.get("asset_id") for r in group if r.get("asset_id")), ""),
            "status": status, "kind": kind, "rfr": category,
            "last_complete_at": completion.isoformat(timespec="seconds") if completion else "",
            "closed_at": next((r.get("closed_at") for r in group if r.get("closed_at")), ""),
            "techs": techs, "remarks": remarks,
            "reported_issue": next((r.get("reported_issue") for r in group if r.get("reported_issue")), ""),
            "cross_shift": cross_shift, "outside_window": outside,
            "action": action, "owner_shift": owner or "",
        })
    return results

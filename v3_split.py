"""S4/S5 attribution by technician work time, not administrative WO closure.

S4 owns 17:30-18:00 overlap; S5 owns 18:00-06:00, with a confirmed
technician grace through 06:30. Ambiguous cross-shift work requires MST review.
"""
from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime, time, timedelta


def parse_close(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def owner_for_close(value, day, confirmed_s5=False):
    """Map a timestamp to S4, S5, or None; name retained for API compatibility."""
    when = parse_close(value)
    if when is None:
        return None
    if isinstance(day, str):
        day = date.fromisoformat(day)
    s4_start = datetime.combine(day, time(5, 30))
    s4_end = datetime.combine(day, time(18, 0))
    s5_end = datetime.combine(day + timedelta(days=1), time(6, 0))
    grace_end = datetime.combine(day + timedelta(days=1), time(6, 30))
    if s4_start <= when < s4_end:
        return "S4"
    if s4_end <= when < s5_end:
        return "S5"
    if s5_end <= when <= grace_end and confirmed_s5:
        return "S5"
    return None


def _owners(items, field, day, confirmed):
    values = [parse_close(r.get(field)) for r in items]
    values = [v for v in values if v is not None]
    return {owner_for_close(v, day, confirmed_s5=any(
        r.get("tech_name") in confirmed and parse_close(r.get(field)) == v
        for r in items)) for v in values} if values else set()


def assign(rows, selected_split, shift_code, shift_day, decisions=None,
           confirmed_s5_techs=None):
    """Credit a completed WO to one shift using actual technician work evidence.

    Precedence: labor start + equipment In Service, then either available
    work timestamp with cross-checks against WO opening/completion, then
    manual review. WO close is never allowed to override clear work times.
    """
    if shift_code not in ("S4", "S5") or not selected_split:
        return rows, []
    decisions = decisions or {}
    selected = set(selected_split)
    confirmed = set(confirmed_s5_techs or ())
    by_wo = defaultdict(list)
    for row in rows:
        status = str(row.get("status", "")).strip().casefold()
        if row.get("tech_name") in selected and status in ("closed", "completed"):
            by_wo[str(row.get("wo_number", ""))].append(row)
    ownership = {}
    pending = []
    for wo, items in by_wo.items():
        start = _owners(items, "start_at", shift_day, confirmed)
        service = _owners(items, "in_service_at", shift_day, confirmed)
        opened = _owners(items, "opened_at", shift_day, confirmed)
        closed = _owners(items, "closed_at", shift_day, confirmed)
        last_complete = _owners(items, "last_complete_at", shift_day, confirmed)
        owner = None
        reason = ""
        source = ""
        # Multiple labor rows can have different timestamps; agreement on
        # owning shift is sufficient, but cross-shift evidence needs review.
        if len(start) > 1 or len(service) > 1 or (start and service and start != service):
            reason = "Technician work or equipment return spans shifts"
        elif start and None not in start and len(start) == 1:
            candidate = next(iter(start))
            if service and (None in service or service != start):
                reason = "Equipment return conflicts with technician start"
            elif not service and closed and closed != start:
                reason = "No return time; administrative closure differs from work shift"
            else:
                owner, source = candidate, "Technician Start Date + In Service" if service else "Technician Start Date"
        elif service and None not in service and len(service) == 1:
            candidate = next(iter(service))
            if opened and None not in opened and opened != service:
                reason = "No technician start; WO opened on another shift"
            else:
                owner, source = candidate, "In Service Date / Time"
        elif start or service:
            reason = "Work timestamp outside configured shift or unconfirmed S5 grace"
        elif opened and len(opened) == 1 and None not in opened and closed and opened == closed:
            # Opening time alone is not evidence that a technician finished
            # the repair. Require MST review even if administrative times agree.
            reason = "Only WO opened and administrative close times available"
        else:
            reason = "Missing technician work and equipment return times"
        if owner is None and decisions.get(wo) in ("S4", "S5"):
            owner, source = decisions[wo], "MST ownership decision"
        if owner is None:
            pending.append({
                "wo_number": wo,
                "asset_id": items[0].get("asset_id", ""),
                "closed_at": items[0].get("closed_at") or items[0].get("in_service_at") or "",
                "time_source": source or "Technician / In Service / Opened / Closed",
                "technicians": ", ".join(sorted({r.get("tech_name", "") for r in items})),
                "reason": reason,
            })
        ownership[wo] = owner
    accepted = []
    for row in rows:
        wo = str(row.get("wo_number", ""))
        if row.get("tech_name") not in selected or wo not in by_wo:
            accepted.append(row)
        elif ownership.get(wo) == shift_code:
            accepted.append(row)
    return accepted, pending

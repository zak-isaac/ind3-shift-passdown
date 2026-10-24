"""S4/S5 WO attribution with S4 overlap priority and confirmed S5 grace."""
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
    """Return owning shift or None when an MST decision is required."""
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


def assign(rows, selected_split, shift_code, shift_day, decisions=None,
           confirmed_s5_techs=None):
    """Filter completed WO credit once per WO; unresolved ownership needs review.

    For weekend shifts selected_split should contain all selected technicians.
    A 06:00-06:30 S5 grace requires an explicitly confirmed technician.
    In Service is only a provisional fallback for missing WO Closed time.
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
        closed = {str(r["closed_at"]) for r in items if parse_close(r.get("closed_at"))}
        in_service = {str(r["in_service_at"]) for r in items if parse_close(r.get("in_service_at"))}
        source = "WO Closed Date/Time" if closed else "In Service Date / Time" if in_service else "Missing timestamp"
        times = closed or in_service
        is_confirmed = any(r.get("tech_name") in confirmed for r in items)
        timestamp = next(iter(times)) if len(times) == 1 else ""
        owner = owner_for_close(timestamp, shift_day, confirmed_s5=is_confirmed) if timestamp else None
        if owner is None and decisions.get(wo) in ("S4", "S5"):
            owner = decisions[wo]
        if owner is None:
            pending.append({
                "wo_number": wo,
                "asset_id": items[0].get("asset_id", ""),
                "closed_at": timestamp,
                "time_source": source,
                "technicians": ", ".join(sorted({r.get("tech_name", "") for r in items})),
                "reason": "Conflicting timestamps" if len(times) > 1 else
                          "Missing completion timestamp" if not times else
                          "Outside shift window or S5 grace not confirmed",
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

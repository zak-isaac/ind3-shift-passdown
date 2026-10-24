"""S1-S5 WO ownership attribution by technician work time, not administrative
WO closure.

Ported from github.com/zak-isaac/ind3-shift-passdown (commit 22cd829) and
adapted to this codebase: the original engine only considers `start_at` and
`in_service_at` as completion evidence; here `updated_at` ("Update Date /
Time", 2026-10-23 facility feedback -- more reliable than "WO Closed
Date/Time") is also tried as a fallback evidence source alongside
`in_service_at`, since it plays the same "when was this really touched"
role for this facility's export.

S4 owns 17:30-18:00 overlap; S5 owns 18:00-06:00, with a confirmed
technician grace through 06:30. A technician's confirmed shift and nearby
unambiguous labor start resolve S1/S2, S2/S3, and S3/S4 overlaps the same
way. A later administrative WO close never transfers work ownership.
Ambiguous cross-shift work surfaces to the MST for an explicit shift choice
via v3_import_review.py, instead of being guessed either way.
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


def _windows(when):
    """Return all scheduled shift/date pairs covering a work timestamp."""
    found = []
    for day in (when.date() - timedelta(days=1), when.date()):
        if day.weekday() < 4:
            spec = (("S1", time(5, 30), time(16), False),
                    ("S2", time(12), time(22, 30), False),
                    ("S3", time(20, 30), time(7), True))
        else:
            spec = (("S4", time(5, 30), time(18), False),
                    ("S5", time(17, 30), time(6), True))
        for shift, start, end, overnight in spec:
            a = datetime.combine(day, start)
            b = datetime.combine(day + timedelta(days=1) if overnight else day, end)
            if a <= when < b:
                found.append((shift, day.isoformat()))
            elif shift == "S5" and b <= when <= b + timedelta(minutes=30):
                found.append((shift, day.isoformat()))
    return found


def _resolve(when, tech, work_history, roster_shifts, confirmed_s5=False):
    options = _windows(when)
    # S5 grace is an overlap, not an automatic S4 assignment.
    if confirmed_s5:
        yesterday = when.date() - timedelta(days=1)
        grace = ("S5", yesterday.isoformat())
        if grace in options and datetime.combine(when.date(), time(6)) <= when <= datetime.combine(when.date(), time(6, 30)):
            return grace
    if len(options) == 1:
        return options[0]
    if not options:
        return None
    # Explicit, shift-date-scoped roster takes precedence in overlaps.
    roster = roster_shifts.get((tech, when.date().isoformat()))
    if roster is None:
        roster = roster_shifts.get(tech)
    if roster:
        hits = [o for o in options if o[0] == roster or o == roster]
        if len(hits) == 1:
            return hits[0]
    # An earlier unambiguous work start by the same technician is evidence
    # of their continuing shift, not proof of an unrecorded handover.
    previous = [(dt, pair) for dt, pair in work_history.get(tech, [])
                if dt < when and when - dt <= timedelta(hours=8) and pair in options]
    if previous:
        return max(previous, key=lambda x: x[0])[1]
    return None


def _completion_evidence(r):
    """Equipment-return/record-touch evidence, in order of specificity.
    in_service_at (equipment physically returned to service) is the most
    direct signal; updated_at ("Update Date / Time") is this facility's
    reliable real-touch signal and is tried next when in_service_at is
    absent. WO Closed Date/Time is deliberately never used here -- it can
    be administratively stale and must never transfer work ownership.
    """
    return parse_close(r.get("in_service_at")) or parse_close(r.get("updated_at"))


def assign(rows, selected_split, shift_code, shift_day, decisions=None,
           confirmed_s5_techs=None, roster_shifts=None):
    """Select completed WOs for one S1-S5 shift, using technician work evidence.

    A technician's confirmed shift and nearby unambiguous labor start resolve
    overlaps. A later administrative WO close never transfers work ownership.

    Returns (accepted_rows, pending_review_items, owned_elsewhere_count).
    `owned_elsewhere_count` is the number of distinct WOs the evidence
    confidently placed in a DIFFERENT, specific shift -- common when a Cetaris
    export spans multiple shifts/days and most of it was already claimed by
    an earlier shift's import. Not ambiguous, so not `pending`, but worth
    surfacing in the import notice so "0 new entries" doesn't read as broken.
    """
    if shift_code not in ("S1", "S2", "S3", "S4", "S5") or not selected_split:
        return rows, [], 0
    day = shift_day.isoformat() if isinstance(shift_day, date) else str(shift_day)
    selected = set(selected_split)
    decisions = decisions or {}
    roster_shifts = roster_shifts or {}
    confirmed = set(confirmed_s5_techs or ())
    history = defaultdict(list)
    by_wo = defaultdict(list)
    for r in rows:
        tech = r.get("tech_name", "")
        when = parse_close(r.get("start_at"))
        if when and len(_windows(when)) == 1:
            history[tech].append((when, _windows(when)[0]))
        if tech in selected and str(r.get("status", "")).strip().casefold() in ("closed", "completed"):
            by_wo[str(r.get("wo_number", ""))].append(r)
    ownership = {}
    pending = []
    for wo, items in by_wo.items():
        starts = []
        ambiguous = False
        for r in items:
            dt = parse_close(r.get("start_at"))
            if dt:
                label = _resolve(dt, r.get("tech_name", ""), history, roster_shifts,
                                 confirmed_s5=r.get("tech_name", "") in confirmed)
                if label:
                    starts.append((dt, r.get("tech_name", ""), label))
                else:
                    ambiguous = True
        owner = None
        reason = ""
        if starts and not ambiguous:
            shifts = {x[2] for x in starts}
            if len(shifts) == 1:
                owner = starts[0][2]
            elif len({x[1] for x in starts}) > 1:
                # Distinct technician handover: latest labor start owns finish.
                owner = max(starts, key=lambda x: x[0])[2]
            else:
                reason = "Same technician worked across shifts; confirm finish owner"
        elif ambiguous:
            reason = "Overlapping technician start without confirmed shift evidence"
        else:
            labels = [_resolve(_completion_evidence(r), r.get("tech_name", ""),
                               history, roster_shifts, confirmed_s5=r.get("tech_name", "") in confirmed)
                      for r in items if _completion_evidence(r)]
            labels = [v for v in labels if v]
            if len(set(labels)) == 1:
                owner = labels[0]
            else:
                reason = "Missing or conflicting work-start and in-service evidence"
        # A recorded equipment return during the technician's scheduled
        # shift (including S3/S4 overlap) does not transfer ownership.
        if owner:
            for r in items:
                service = _completion_evidence(r)
                if service and owner not in _windows(service) and not (
                    owner[0] == "S5" and r.get("tech_name") in confirmed and
                    datetime.combine(date.fromisoformat(owner[1]) + timedelta(days=1), time(6)) <= service <=
                    datetime.combine(date.fromisoformat(owner[1]) + timedelta(days=1), time(6, 30))
                ):
                    owner = None
                    reason = "Equipment returned outside technician's owning shift"
                    break
        override = decisions.get(wo)
        if override:
            if isinstance(override, (tuple, list)) and len(override) == 2:
                owner = tuple(override)
            elif isinstance(override, str):
                candidates = [(s, d) for r in items for field in
                              ("start_at", "in_service_at", "updated_at", "opened_at")
                              for dt in [parse_close(r.get(field))] if dt
                              for s, d in _windows(dt) if s == override]
                if candidates:
                    owner = candidates[0]
        ownership[wo] = owner
        import os as _os
        if _os.environ.get("V3_SPLIT_DEBUG"):
            print(f"[v3-split-diag] wo={wo} techs={[r.get('tech_name') for r in items]} "
                  f"starts={[r.get('start_at') for r in items]} owner={owner} reason={reason!r}",flush=True)
        if owner is None:
            # Do not ask S4 MST to review an S3 WO whose technician was not
            # selected for S4. For genuinely ambiguous selected labor, review.
            evidence = [parse_close(r.get(k)) for r in items for k in
                        ("start_at", "in_service_at", "updated_at", "opened_at")]
            relevant = any((shift_code, day) in _windows(dt) for dt in evidence if dt)
            if not any(evidence):
                # A selected completed WO with no timing evidence must never
                # disappear silently; MST confirms its ownership.
                relevant = True
            if shift_code == "S5" and any(r.get("tech_name") in confirmed for r in items):
                relevant = relevant or any(
                    datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(6)) <= dt <=
                    datetime.combine(date.fromisoformat(day) + timedelta(days=1), time(6, 30))
                    for dt in evidence if dt)
            if relevant:
                pending.append({
                    "wo_number": wo, "asset_id": items[0].get("asset_id", ""),
                    "closed_at": items[0].get("updated_at") or items[0].get("closed_at") or "",
                    "time_source": "Technician Start / In Service / Update Date / Roster",
                    "technicians": ", ".join(sorted({r.get("tech_name", "") for r in items})),
                    "reason": reason,
                })
    accepted = []
    for r in rows:
        if r.get("tech_name") not in selected:
            continue
        status = str(r.get("status", "")).strip().casefold()
        if status in ("closed", "completed"):
            if ownership.get(str(r.get("wo_number", ""))) == (shift_code, day):
                accepted.append(r)
        else:
            # OOS is shift-specific too; a breakdown opened by another shift
            # must not leak into the active shift's status tracking.
            when = parse_close(r.get("start_at")) or parse_close(r.get("opened_at"))
            if when and _resolve(when, r.get("tech_name", ""), history, roster_shifts,
                                 confirmed_s5=r.get("tech_name", "") in confirmed) == (shift_code, day):
                accepted.append(r)
    # Distinct WOs the evidence confidently placed in a DIFFERENT, specific
    # shift (e.g. yesterday's S4, already recorded there) -- not ambiguous,
    # so not `pending`, but silently returning 0 accepted with no count at
    # all reads as "something's broken" to an MST re-uploading a file that
    # genuinely overlaps an already-covered shift. Surfaced by v3_routes.py
    # in the import confirmation notice.
    owned_elsewhere = sum(1 for owner in ownership.values() if owner and owner != (shift_code, day))
    return accepted, pending, owned_elsewhere

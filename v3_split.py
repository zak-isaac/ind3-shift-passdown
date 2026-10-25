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


def assign(rows, selected_split, shift_code, shift_day, decisions=None,
           confirmed_s5_techs=None, roster_shifts=None):
    """Select completed WOs for one S1-S5 shift, using technician work evidence.

    A technician's confirmed shift and nearby unambiguous labor start resolve
    overlaps. A later administrative WO close never transfers work ownership.
    """
    if shift_code not in ("S1", "S2", "S3", "S4", "S5") or not selected_split:
        return rows, []
    day = shift_day.isoformat() if isinstance(shift_day, date) else str(shift_day)
    selected = set(selected_split)
    decisions = decisions or {}
    roster_shifts = roster_shifts or {}
    confirmed = set(confirmed_s5_techs or ())
    history = defaultdict(list)
    by_wo = defaultdict(list)
    # The selected technician controls which WOs are eligible to appear.
    # Ownership must inspect ALL labor participants on those WOs, including
    # incoming-shift collaborators who are not selected for this passdown.
    participating_wos = {
        str(r.get("wo_number", "")) for r in rows
        if r.get("tech_name") in selected and
           str(r.get("status", "")).strip().casefold() in ("closed", "completed")
    }
    for r in rows:
        tech = r.get("tech_name", "")
        when = parse_close(r.get("start_at"))
        if when and len(_windows(when)) == 1:
            history[tech].append((when, _windows(when)[0]))
        if (str(r.get("wo_number", "")) in participating_wos and
            str(r.get("status", "")).strip().casefold() in ("closed", "completed")):
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
            # Cetaris can have a recorded WO Last Complete timestamp without
            # an In Service timestamp or a fresh labor Start Date. This is
            # completion evidence, not delayed administrative WO closure.
            labels = [
                _resolve(finished, r.get("tech_name", ""), history, roster_shifts,
                         confirmed_s5=r.get("tech_name", "") in confirmed)
                for r in items
                for finished in [parse_close(r.get("in_service_at")) or
                                 parse_close(r.get("last_complete_at"))]
                if finished
            ]
            labels = [v for v in labels if v]
            if len(set(labels)) == 1:
                owner = labels[0]
            else:
                reason = "Missing or conflicting work-start and in-service evidence"
        # Shared S3 work completed before the 07:00 boundary belongs to S3,
        # even if an arriving S1/S4 technician has the latest labor start.
        # Require actual return/completion evidence instead of guessing from
        # an administratively delayed WO close.
        finish_times = [
            parse_close(r.get("in_service_at")) or parse_close(r.get("last_complete_at"))
            for r in items
        ]
        final_times = [value for value in finish_times if value]
        final_work_time = max(final_times) if final_times else None
        if owner is None and final_work_time and _windows(final_work_time):
            reason = ("Recorded completion needs shift-owner review because "
                      "the technician's work-start shift is ambiguous")
        participant_shifts = {pair for _, _, pair in starts}
        s3_pairs = [pair for pair in participant_shifts if pair[0] == "S3"]
        s3_priority = False
        incoming_pairs = [pair for pair in participant_shifts if pair[0] in ("S1", "S4")]
        if s3_pairs and incoming_pairs:
            s3_pair = s3_pairs[0]
            s3_day = date.fromisoformat(s3_pair[1])
            end_s3 = datetime.combine(s3_day + timedelta(days=1), time(7))
            if final_work_time is None:
                owner = None
                reason = "Shared S3 work needs a recorded completion or In Service time"
            elif final_work_time < end_s3 and s3_pair in _windows(final_work_time):
                owner = s3_pair
                s3_priority = True
            # After 07:00 the later-shift attribution remains subject to
            # technician evidence and the normal conflict checks.

        # For S4/S5, equipment actually returned after 18:00 belongs to S5
        # even if its technician began the WO during S4. Finishing during
        # the S4 overlap before 18:00 remains S4 work.
        s4_pairs = [pair for pair in participant_shifts if pair[0] == "S4"]
        if final_work_time and s4_pairs and not s3_priority:
            candidates = _windows(final_work_time)
            s5_candidates = [pair for pair in candidates if pair[0] == "S5"]
            if s5_candidates and not any(pair in candidates for pair in s4_pairs):
                owner = s5_candidates[0]
            elif any(pair in candidates for pair in s4_pairs):
                # S4 has priority until 18:00 in the shared evening window.
                owner = next(pair for pair in s4_pairs if pair in candidates)
        # Never silently assign an older work-start shift when recorded
        # completion is in a different shift. Such cross-date WOs require
        # an explicit MST owner decision, rather than disappearing on S5.
        if owner and final_work_time and owner not in _windows(final_work_time):
            owner = None
            reason = "Work began on another shift; recorded completion needs shift-owner review"
        # A recorded equipment return during the technician's scheduled
        # shift (including S3/S4 overlap) does not transfer ownership.
        if owner:
            for r in items:
                service = parse_close(r.get("in_service_at"))
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
                              ("start_at", "in_service_at", "last_complete_at", "opened_at")
                              for dt in [parse_close(r.get(field))] if dt
                              for s, d in _windows(dt) if s == override]
                if candidates:
                    owner = candidates[0]
        ownership[wo] = owner
        if owner is None:
            # Do not ask S4 MST to review an S3 WO whose technician was not
            # selected for S4. For genuinely ambiguous selected labor, review.
            evidence = [parse_close(r.get(k)) for r in items for k in
                        ("start_at", "in_service_at", "last_complete_at", "opened_at")]
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
                    "closed_at": items[0].get("closed_at") or "",
                    "time_source": "Technician Start / In Service / Roster",
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
    return accepted, pending

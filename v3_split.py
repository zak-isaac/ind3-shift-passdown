"""S4/S5 attribution by technician work time, not administrative WO closure.

S4 owns 17:30-18:00 overlap; S5 owns 18:00-06:00, with a confirmed
technician grace through 06:30. Unverifiable completion is excluded and explained by the import audit.
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


def _person_key(name):
    """Match 'Last, First' with 'First Last' without fuzzy name guessing."""
    import re
    return tuple(sorted(re.findall(r"[a-z0-9]+", str(name or "").casefold())))


def _grace_requires_confirmation(when, owner, confirmed_tech, tech):
    if not owner or owner[0] != "S5":
        return False
    day = date.fromisoformat(owner[1]) + timedelta(days=1)
    return (datetime.combine(day, time(6)) <= when <=
            datetime.combine(day, time(6, 30)) and tech not in confirmed_tech)


def _choose_completion_owner(when, tech, items, history, roster_shifts, confirmed):
    """Resolve recorded *work completion*, never an administrative close.

    Overlaps retain established site policy: S4 until 18:00, S3 until
    07:00 when S3 technician labor is documented, and confirmed S5 grace.
    """
    options = _windows(when)
    options = [pair for pair in options
               if not _grace_requires_confirmation(when, pair, confirmed, tech)]
    if not options:
        return None

    # On the weekend evening handover, S4 retains credits through 17:59.
    s4 = [pair for pair in options if pair[0] == "S4" and
          when.date() == date.fromisoformat(pair[1]) and
          time(17, 30) <= when.time() < time(18)]
    if s4:
        return s4[0]

    # S3 gets joint work before its 07:00 boundary, provided the export
    # actually contains an S3 technician / labor start (no inferred claim).
    s3 = [pair for pair in options if pair[0] == "S3"]
    if s3:
        def has_s3_evidence(pair):
            for r in items:
                worker = r.get("tech_name") or ""
                home = roster_shifts.get(worker)
                start = parse_close(r.get("start_at"))
                if home == "S3" and start and start <= when:
                    return True
                if start and start <= when and pair in _windows(start):
                    # A unique S3 start is strong; no reliance on admin close.
                    if len(_windows(start)) == 1:
                        return True
            return False
        for pair in s3:
            if has_s3_evidence(pair):
                return pair

    # Prefer completing technician's shift when two daypart windows overlap.
    result = _resolve(when, tech, history, roster_shifts,
                      confirmed_s5=tech in confirmed)
    if result in options:
        return result
    if len(options) == 1:
        return options[0]
    return None


def _actual_completion(items):
    """Determine whom Cetaris says completed the WO, and at what time.

    'Completed By' can describe an administrative action. Trust the WO Last
    Complete timestamp only when that person is an actual labor participant.
    In Service is an equipment-return fallback if consistent with the
    participating technician's work start. Never consult WO Closed Date/Time
    to assign a shift or assert a technician completed the repair.
    """
    participant_by_key = {
        _person_key(r.get("tech_name")): r.get("tech_name")
        for r in items if _person_key(r.get("tech_name"))
    }
    verified = []
    seen = set()
    for r in items:
        completer_key = _person_key(r.get("completed_by"))
        technician = participant_by_key.get(completer_key)
        moment = parse_close(r.get("last_complete_at"))
        if technician and moment:
            key = ("technician_completion", technician, moment)
            if key not in seen:
                verified.append((moment, technician,
                                 "WO Last Complete + matching Completed By"))
                seen.add(key)
    if verified:
        # When a separate, earlier In Service event is supported by the
        # completer's labor, it marks the real equipment return. Do not
        # transfer that work merely because WO Last Complete equals a later
        # administrative closure timestamp.
        returns = []
        for r in items:
            if _person_key(r.get("tech_name")) != _person_key(verified[-1][1]):
                continue
            start = parse_close(r.get("start_at"))
            service = parse_close(r.get("in_service_at"))
            if service and (start is None or start <= service):
                returns.append((service, r.get("tech_name"), "In Service + technician labor"))
        if returns:
            return max(returns, key=lambda p:p[0])
        return max(verified,key=lambda p:p[0])

    # Missing / mismatched Completed By is not evidence that the closer
    # performed the work. Require an In Service timestamp compatible with
    # labor, not one preceding the labor start (as in BALER0003 WO 27999099).
    returns = []
    for r in items:
        service = parse_close(r.get("in_service_at"))
        start = parse_close(r.get("start_at"))
        if service and (start is None or start <= service):
            returns.append((service,r.get("tech_name") or "", "In Service + technician labor"))
    if returns:
        # When Completed By is a different administrative user, Cetaris's
        # Last Technician is a better indication of the actual final worker
        # than the order of labor rows in the export.
        last_workers = {_person_key(r.get("last_technician")) for r in items
                        if _person_key(r.get("last_technician")) in participant_by_key}
        if len(last_workers) == 1:
            finisher = next(iter(last_workers))
            finisher_returns = [result for result in returns
                                if _person_key(result[1]) == finisher]
            if finisher_returns:
                return max(finisher_returns,key=lambda p:p[0])
        # Different technicians with the same equipment-return timestamp
        # are not proof of which one finished it. Return one candidate only
        # when it is the sole supported worker (the owning shift must still
        # be independently verified).
        workers = {_person_key(r[1]) for r in returns}
        if len(workers)==1:
            return max(returns,key=lambda p:p[0])
        # If the finish is unambiguously inside one scheduled shift, credit
        # the WO once without fabricating personal completion attribution.
        shift_sets = {tuple(_windows(r[0])) for r in returns}
        if len(shift_sets)==1 and len(next(iter(shift_sets)))==1:
            return max(returns,key=lambda p:p[0])
        return None
    return None


def assign(rows, selected_split, shift_code, shift_day, decisions=None,
           confirmed_s5_techs=None, roster_shifts=None):
    """Attribute finished WOs using documented technician + completion time.

    No ownership dialog or MST guesswork. Ambiguous/incomplete evidence
    produces an ignored-WO audit reason; it cannot block valid WOs.
    Legacy decisions are intentionally ignored: an administrative choice
    cannot override Cetaris technician completion evidence.
    """
    if shift_code not in ("S1", "S2", "S3", "S4", "S5") or not selected_split:
        return rows, []
    day = shift_day.isoformat() if isinstance(shift_day,date) else str(shift_day)
    selected = set(selected_split)
    roster_shifts = roster_shifts or {}
    confirmed = set(confirmed_s5_techs or ())
    history = defaultdict(list)
    by_wo = defaultdict(list)
    selected_completed = {
        str(r.get("wo_number") or "") for r in rows
        if r.get("tech_name") in selected and
        str(r.get("status") or "").strip().casefold() in ("closed","completed")
    }
    # Include all participants in each selected WO when deciding its owner.
    for r in rows:
        tech = str(r.get("tech_name") or "")
        begin = parse_close(r.get("start_at"))
        if begin and len(_windows(begin)) == 1:
            history[tech].append((begin, _windows(begin)[0]))
        if (str(r.get("wo_number") or "") in selected_completed and
            str(r.get("status") or "").strip().casefold() in ("closed","completed")):
            by_wo[str(r.get("wo_number") or "")].append(r)

    owners = {}
    pending = []
    for wo, items in by_wo.items():
        evidence = _actual_completion(items)
        owner = None
        if evidence:
            when, completing_tech, source = evidence
            if completing_tech not in selected:
                reason = "Completing technician is not selected for this shift"
                owner = None
            else:
                owner = _choose_completion_owner(
                    when, completing_tech, items, history, roster_shifts, confirmed)
                reason = ("Completion timestamp falls in overlapping shifts without reliable technician shift evidence"
                          if owner is None else "")
            if owner is None and not reason:
                reason = "Completion timestamp lacks reliable technician shift evidence"
            elif _grace_requires_confirmation(when, owner, confirmed, completing_tech):
                owner = None
                reason = "S5 completion during grace requires confirmed S5 technician"
            else:
                reason = ""
        else:
            reason = ("No verified technician completion timestamp. "
                      "Administrative WO closure and a different Completed By person do not establish ownership")
            source = "None"
        owners[wo] = owner
        if owner is None:
            # List only WOs plausibly relevant to the current shift; other
            # shifts' WOs are still visible in the general audit after import.
            times = [parse_close(r.get(f)) for r in items for f in
                     ("start_at","in_service_at","last_complete_at")]
            relevant = (not any(times) or
                        any((shift_code,day) in _windows(t) for t in times if t))
            if relevant:
                pending.append({
                    "wo_number":wo, "asset_id":items[0].get("asset_id",""),
                    "closed_at":items[0].get("closed_at") or "",
                    "time_source":source,
                    "technicians":", ".join(sorted({r.get("tech_name","") for r in items})),
                    "completed_by":", ".join(sorted({r.get("completed_by","") for r in items
                                                  if r.get("completed_by")})),
                    "reason":reason,
                })

    accepted = []
    for r in rows:
        tech = r.get("tech_name")
        if tech not in selected:
            continue
        wo = str(r.get("wo_number") or "")
        status = str(r.get("status") or "").strip().casefold()
        if status in ("closed","completed"):
            if owners.get(wo) == (shift_code, day):
                accepted.append(r)
        elif status == "in process":
            # Breakdown OOS retains start-time ownership, because an active
            # breakdown naturally has no completion timestamp yet.
            when = parse_close(r.get("start_at")) or parse_close(r.get("opened_at"))
            if when and _resolve(when,tech,history,roster_shifts,
                                 confirmed_s5=tech in confirmed) == (shift_code, day):
                accepted.append(r)
    return accepted, pending

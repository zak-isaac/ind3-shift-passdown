"""S4/S5 split-worker WO attribution by administrative WO close timestamp.

Wired into v3_routes.py's confirm_import() for S4/S5 imports. Prefers
"Update Date / Time" over "WO Closed Date/Time" as the real close signal
when both exist (2026-10-23 facility feedback: Update Date/Time reflects
when the record was actually last touched/closed, more reliably than WO
Closed Date/Time) -- see owner_for_close() callers below.
"""
from __future__ import annotations
from datetime import date, datetime, time, timedelta

def parse_close(value):
    if isinstance(value,datetime): return value
    if not value: return None
    try: return datetime.fromisoformat(str(value))
    except (ValueError,TypeError): return None

def owner_for_close(value, day):
    """Return S4, S5, or None for overlap, missing, or outside windows."""
    when=parse_close(value)
    if when is None: return None
    if isinstance(day,str): day=date.fromisoformat(day)
    s4_start=datetime.combine(day,time(5,30))
    s4_end=datetime.combine(day,time(18,0))
    s5_start=datetime.combine(day,time(17,30))
    s5_end=datetime.combine(day+timedelta(days=1),time(6,0))
    if s4_start<=when<s5_start: return "S4"
    if s4_end<=when<s5_end: return "S5"
    return None

def assign(rows, selected_split, shift_code, shift_day, decisions=None):
    """Return filtered rows and WOs requiring explicit MST ownership selection."""
    if shift_code not in ("S4","S5") or not selected_split:
        return rows, []
    decisions=decisions or {}
    pending={}
    accepted=[]
    for r in rows:
        if r.get("tech_name") not in selected_split:
            accepted.append(r)
            continue
        wo=r.get("wo_number","")
        status=str(r.get("status","")).casefold().strip()
        # OOS tracking may be shared across shifts; completed credit is split.
        if status not in ("closed","completed"):
            accepted.append(r)
            continue
        owner=owner_for_close(r.get("updated_at") or r.get("closed_at"),shift_day)
        if owner is None:
            if decisions.get(wo) in ("S4","S5"):
                owner=decisions[wo]
            else:
                pending[wo]={"wo_number":wo,"asset_id":r.get("asset_id",""),
                             "closed_at":r.get("closed_at","")}
                continue
        if owner==shift_code:
            accepted.append(r)
    return accepted,list(pending.values())

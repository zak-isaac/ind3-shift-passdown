"""Signed V3 shift-binding; associate number is identification, NOT proof of identity.
A corporate identity provider should replace this when deployed beyond trusted workstations.

The `elevated` bit carried in the signed cookie is how a personal master
credential (a real associate number that's also configured as a master code
in main.py's _MASTER_CODES, e.g. Isaac's own 2759) stays recognized as
manager-equivalent on every subsequent request, not just the login response.
It is only ever set server-side in v3_routes.py AFTER
_verify_master()/_is_master_credential() already approved the associate
number -- the HMAC signature on this cookie is the real trust boundary, so
re-deriving is_manager from it on read is safe: a client cannot forge
elevated=1 without the server's signing secret.

NOTE: an earlier version of this file additionally gated manager access
behind unsigned IND3_V3_MASTER_CODE / IND3_V3_PERSONAL_MASTER_ASSOC
environment variables, ported from a GitHub operational branch built for a
login flow that doesn't exist here. Nothing in this app's actual login route
ever configured those env vars, so it silently locked out every
manager/master login (see git history, 2026-10-23). Removed -- the signed
elevated flag is sufficient on its own.
"""
from __future__ import annotations
import base64, hashlib, hmac, os, secrets, time

COOKIE="ind3_v3_mst_session"  # retain cookie name for migration from V3 previews
_SECRET=os.environ.get("IND3_V3_SESSION_SECRET", "").encode() or secrets.token_bytes(32)

def sign(associate, shift_id=0, elevated=False):
    data=f"{associate}:{int(shift_id)}:{int(time.time())}:{int(bool(elevated))}"
    value=base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")
    return value+"."+hmac.new(_SECRET,value.encode(),hashlib.sha256).hexdigest()

def read(request, roster):
    try:
        encoded, mac=request.cookies.get(COOKIE,"").rsplit(".",1)
        if not hmac.compare_digest(mac,hmac.new(_SECRET,encoded.encode(),hashlib.sha256).hexdigest()): return None
        parts=base64.urlsafe_b64decode(encoded+"="*(-len(encoded)%4)).decode().split(":")
        if len(parts) not in (3,4): return None
        assoc,sid,issued=parts[:3]
        elevated=parts[3]=="1" if len(parts)==4 else False
        if not (0 <= time.time()-int(issued) < 12*3600): return None
        person=roster.get(assoc)
        if not person: return None
        # A signed elevated=1 bit means v3_routes.py already ran
        # _verify_master()/_is_master_credential() on this associate number
        # before issuing the cookie -- safe to grant manager-equivalent
        # rights here even if the roster itself doesn't flag them a manager
        # (e.g. a personal master credential like Isaac's own 2759).
        if elevated and not person.get("is_manager"):
            person={**person,"is_manager":True}
        return {"associate":assoc,"shift_id":int(sid),"profile":person,"elevated":elevated}
    except (ValueError,TypeError,KeyError,UnicodeDecodeError): return None

def owns(shift, session):
    if not shift or not session or not shift.get("is_active"): return False
    if session["profile"].get("is_manager"): return True
    opener=(shift.get("opened_by_assoc") or "").strip()
    if opener: return opener == session["associate"]
    # Older V2 records sometimes have no opening associate ID.
    # Allow an exact display-name match ONLY for legacy shifts.
    name=(session["profile"].get("display_name") or session["profile"].get("full_name") or "").strip().casefold()
    return bool(name) and (shift.get("lead_name") or "").strip().casefold()==name

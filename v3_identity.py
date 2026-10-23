"""Signed V3 shift-binding; associate number is identification, NOT proof of identity.
A corporate identity provider should replace this when deployed beyond trusted workstations.
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
        # 8972 / synthetic manager must not become an associate-number sign-in
        # unless this session was verified + elevated via the master-code flow.
        if not person: return None
        if person.get("is_manager"):
            if assoc!="8972" or not elevated or not os.environ.get("IND3_V3_MASTER_CODE"):
                return None
        if elevated and not person.get("is_manager"):
            allowed=os.environ.get("IND3_V3_PERSONAL_MASTER_ASSOC","").strip()
            if not allowed or assoc!=allowed or not os.environ.get("IND3_V3_PERSONAL_MASTER_SECRET"):
                return None
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

"""Parallel V3 shift workspace for MST and technician shift starters. V2 report and maintenance handlers remain unchanged.

V3 is opt-in at /v3. No AI path is invoked by this router.
"""
from __future__ import annotations

import json
import uuid
import hashlib
import base64
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse

import db
import cause_lookup
import v3_breakdown
import v3_split
import v3_import_review
from v3_cetaris import parse_excel, select_records, technician_counts, MAX_SIZE


def create_v3_router(templates, is_lead_authed, can_manage_shift, resolve_my_shift,
                     shift_window, lead_redirect, get_mst_roster=None, verify_mst_pin=None,
                     start_eligibility=None, allowed_shift_days=None, now_func=None,
                     get_v2_cookie_value=None, generate_report=None,
                     verify_master_code=None, build_eml_response=None, refresh_roster=None):
    router = APIRouter(prefix="/v3", tags=["Shift V3"])

    # Fallback mirrors main.py's _MASTER_CODES so standalone/test usage of this
    # router (no main.py injection) still behaves correctly. When main.py wires
    # this router in, it should inject its own _is_master_credential instead so
    # there's exactly one source of truth for who holds master access.
    _FALLBACK_MASTER_CODES = {"8972", "2759"}
    _verify_master = verify_master_code or (lambda code: code.strip() in _FALLBACK_MASTER_CODES)

    # Same signed V3 session can be validated by the original V2 handlers.
    from v3_identity import COOKIE as v3_cookie, sign as signed_session, read as read_session, owns as shift_owned_by

    def decode_session(request):
        if get_mst_roster is None:
            return None  # compatible with isolated pre-auth V3 extraction tests
        return read_session(request, get_mst_roster())

    def accessible_shifts(request):
        who=decode_session(request)
        return [shift for shift in db.get_active_shifts() if shift_owned_by(shift,who)]

    def _v3_real_shifts():
        """_v3_live_shifts() minus any test/sandbox shift. Every real
        shift-start slot-occupancy or active-count check must use this, not
        _v3_live_shifts() directly -- a sandbox shift must never block (or
        count toward the cap for) a real technician/MST starting their own
        S1-S5 shift of the same type."""
        return [sh for sh in _v3_live_shifts() if not sh.get("is_test_shift")]

    def set_session_cookies(response, who, sid, elevated=False):
        response.set_cookie(v3_cookie, signed_session(who["associate"],sid,elevated=elevated), max_age=12*3600,
                            httponly=True, samesite="strict", path="/", secure=False)
        # Legacy controls remain usable with the SAME shift bound to V2.
        if get_v2_cookie_value:
            response.set_cookie("lead_auth", get_v2_cookie_value(), httponly=True, samesite="lax", path="/", max_age=12*3600)
            response.set_cookie("lead_shift_id", str(sid), httponly=True, samesite="lax", path="/", max_age=12*3600)
            response.delete_cookie("mgr_session", path="/")

    def _v3_live_shifts():
        now = now_func() if now_func else datetime.now()
        visible = []
        for shift in db.get_active_shifts():
            if shift.get("is_test_shift"):
                # Durable manual-testing sandbox (2026-10-24 request): never
                # auto-closed by the real-shift time-window check below --
                # it isn't a physical shift with a scheduled end time.
                visible.append(shift)
                continue
            try:
                window = shift_window(shift["shift_type"], date.fromisoformat(shift["date"]))
                if not window:
                    continue
                # A shift is considered live from 1h before its scheduled start
                # through 1h after its scheduled end -- matches the real-world
                # handover grace period on both sides, not just at the start.
                if now > window[1] + timedelta(hours=1):
                    db.close_shift(shift["id"])
                elif now < window[0] - timedelta(hours=1):
                    db.close_shift(shift["id"])
                else:
                    visible.append(shift)
            except (ValueError, KeyError, TypeError):
                continue
        return visible

    def _v3_shift_day(code):
        now = now_func() if now_func else datetime.now()
        for day in (now.date(), now.date() - timedelta(days=1)):
            if allowed_shift_days and day.weekday() not in allowed_shift_days.get(code, ()):
                continue
            window = shift_window(code, day)
            if window and window[0] - timedelta(hours=1) <= now <= window[1] + timedelta(hours=1):
                return day
        return None

    def entry_page(request, error="", master_verified=False):
        shifts=_v3_live_shifts()
        now=now_func() if now_func else datetime.now()
        return templates.TemplateResponse(request,"v3/login.html",{
            "error":error,"shifts":shifts,"today":now.date().isoformat(),
            "master_verified":master_verified,
            "eligible_shifts":[code for code in ("S1","S2","S3","S4","S5") if _v3_shift_day(code)],
            "valid_shifts": [code for code,days in (allowed_shift_days or {}).items()
                             if now.date().weekday() in days] or ["S1","S2","S3","S4","S5"],
        })

    @router.get("/login",response_class=HTMLResponse)
    async def v3_login_page(request:Request):
        return entry_page(request)

    @router.post("/login",response_class=HTMLResponse)
    async def v3_login_submit(request:Request,associate_id:str=Form(...),
                              shift_type:str=Form(""),shift_date:str=Form(""),
                              shift_id:int=Form(0),mode:str=Form("start")):
        """V2-style entry: associate number + own shift, no new PIN requirement.

        Login does not authenticate the employee's real-world identity: an
        enterprise SSO provider is required for strong identity assurance.
        """
        if get_mst_roster is None:
            return lead_redirect("/v3")
        associate=associate_id.strip()
        profile=get_mst_roster().get(associate)
        if not profile:
            return entry_page(request,"Associate number not found in the current roster.")
        # The existing test manager uses the same associate-number field.
        # This does not grant access to any other manager profile -- except a
        # personal master credential (main.py's _MASTER_CODES, e.g. Isaac's
        # own 2759), which gets the same manager-equivalent rights as the
        # shared 8972 profile, attributed to the real associate number
        # instead of masquerading as the synthetic "Manager" identity.
        if not profile.get("is_manager") and _verify_master(associate):
            profile={**profile,"is_manager":True}
        if profile.get("is_manager"):
            if not _verify_master(associate):
                return entry_page(request,"This management profile cannot use the test shift selector.")
            response = entry_page(request, master_verified=True)
            set_session_cookies(response, {"associate":associate,"profile":profile}, 0, elevated=True)
            return response
        who={"associate":associate,"profile":profile}
        owned=[sh for sh in _v3_live_shifts() if shift_owned_by(sh,who)]
        if mode=="resume":
            chosen=next((sh for sh in owned if sh["id"]==shift_id),None)
            if not chosen:
                return entry_page(request,"This shift was not started by your associate number.")
            response=RedirectResponse(f"/v3?shift_id={chosen['id']}",status_code=303)
            set_session_cookies(response,who,chosen["id"])
            return response
        if owned:
            chosen = owned[0]
            response = RedirectResponse(f"/v3?shift_id={chosen['id']}", status_code=303)
            set_session_cookies(response, who, chosen["id"])
            return response
        assigned = str(profile.get("shift") or "").strip().upper()
        if assigned not in ("S1","S2","S3","S4","S5"):
            return entry_page(request, "No assigned shift found in the roster.")
        day = _v3_shift_day(assigned)
        if day is None:
            return entry_page(request, f"{assigned} is outside its scheduled shift window.")
        shift_type, shift_date = assigned, day.isoformat()
        # Start a shift. Validate date and shift before allocating any records.
        if not shift_date or not shift_type:
            return entry_page(request,"Select a shift date and shift code.")
        today=(now_func() if now_func else datetime.now()).date()
        try: parsed=date.fromisoformat(shift_date)
        except ValueError: return entry_page(request,"Invalid shift date.")
        if parsed>today or (today-parsed).days>1:
            return entry_page(request,"Start date must be today or yesterday.")
        if shift_type not in ("S1","S2","S3","S4","S5"):
            return entry_page(request,"Invalid shift selection.")
        if allowed_shift_days and parsed.weekday() not in allowed_shift_days.get(shift_type,()):
            return entry_page(request,"Selected shift is not scheduled for this date.")
        # Never create a second instance of a shift code; allow distinct shift
        # codes to overlap, each under their own starter's identity. Test/
        # sandbox shifts are invisible here -- they must never block a real
        # associate from starting their own real shift of the same type.
        if any(sh["shift_type"]==shift_type for sh in _v3_real_shifts()):
            return entry_page(request,f"{shift_type} is already active. Resume your own shift or contact its starter.")
        if len(_v3_real_shifts())>=2:
            return entry_page(request,"Two overlapping shifts are already running.")
        if start_eligibility:
            allowed,is_ot,message=start_eligibility(profile,shift_type,now_func() if now_func else datetime.now())
            if not allowed: return entry_page(request,message or "Shift is not available to this associate.")
        else: is_ot=False
        name=profile.get("display_name") or profile.get("full_name") or "Associate"
        role="mst" if profile.get("is_mst") else "tech"
        try:
            sid=db.start_shift(shift_date,shift_type,name,opened_by_role=role,
                               is_ot=is_ot if role=="mst" else False,opened_by_assoc=associate)
        except ValueError:
            return entry_page(request,"The active-shift limit was reached. Try again.")
        response=RedirectResponse(f"/v3?shift_id={sid}",status_code=303)
        set_session_cookies(response,who,sid,elevated=who.get("elevated",False))
        return response

    @router.post("/master/verify", response_class=HTMLResponse)
    async def verify_master(request:Request, master_code:str=Form(...)):
        entered = master_code.strip()
        if not _verify_master(entered):
            return entry_page(request, "Invalid testing master code.")
        # The roster still needs an is_manager profile to act as; any master
        # code in the set authenticates, but roster lookup stays keyed on 8972
        # since that's the one synthetic manager profile the roster defines.
        profile = (get_mst_roster() or {}).get("8972") if get_mst_roster else None
        if not profile or not profile.get("is_manager"):
            return entry_page(request, "Manager test profile is missing.")
        response = entry_page(request, master_verified=True)
        set_session_cookies(response, {"associate":"8972","profile":profile}, 0, elevated=True)
        return response

    @router.post("/master/select")
    async def master_select(request:Request, shift_type:str=Form(...)):
        who=decode_session(request)
        if not who or not who["profile"].get("is_manager"):
            return RedirectResponse("/v3/login", status_code=303)
        day=_v3_shift_day(shift_type) if shift_type in ("S1","S2","S3","S4","S5") else None
        if not day:
            return entry_page(request, "Shift is outside its working window.", master_verified=True)
        active=_v3_real_shifts()
        chosen=next((sh for sh in active if sh["shift_type"]==shift_type),None)
        if not chosen:
            if len(active)>=2:
                return entry_page(request, "Two shifts are already active.", master_verified=True)
            try:
                sid=db.start_shift(day.isoformat(),shift_type,"Manager",
                                   opened_by_role="mst",is_ot=False,opened_by_assoc="8972")
            except ValueError:
                return entry_page(request, "Unable to start shift.", master_verified=True)
        else:
            sid=chosen["id"]
        response=RedirectResponse(f"/v3?shift_id={sid}",status_code=303)
        set_session_cookies(response,who,sid,elevated=who.get("elevated",False))
        return response

    @router.get("/logout")
    async def v3_logout():
        response=RedirectResponse("/v3/login",status_code=303)
        for key in (v3_cookie,"lead_auth","lead_shift_id","mgr_session"):
            response.delete_cookie(key,path="/")
        return response

    @router.post("/attach/{shift_id}")
    async def attach_shift(request:Request,shift_id:int):
        who=decode_session(request)
        if not who: return RedirectResponse("/v3/login",status_code=303)
        shift=db.get_shift(shift_id)
        if not shift_owned_by(shift,who): raise HTTPException(403,"Shift belongs to another associate")
        response=RedirectResponse(f"/v3?shift_id={shift_id}",status_code=303)
        set_session_cookies(response,who,shift_id)
        return response

    @router.post("/start")
    async def start_shift_v3(request:Request):
        # The old free-form shift selector is retired in V3.
        # Only the verified master-selection flow can override roster assignment.
        raise HTTPException(403, "Use V3 login or verified management access.")

    def ensure_tables():
        # V3 metadata is *sidecar* data so V2 schemas and records are not rewritten.
        with db._conn() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS v3_import_batches (
                  token TEXT PRIMARY KEY, shift_id INTEGER NOT NULL,
                  filename TEXT NOT NULL, created_at TEXT NOT NULL,
                  rows_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v3_entry_meta (
                  entry_id INTEGER PRIMARY KEY,
                  shift_id INTEGER NOT NULL,
                  reported_issue TEXT NOT NULL DEFAULT '',
                  cause_text TEXT NOT NULL DEFAULT '',
                  original_remarks TEXT NOT NULL DEFAULT '[]',
                  mst_edited INTEGER NOT NULL DEFAULT 0,
                  needs_review INTEGER NOT NULL DEFAULT 0,
                  pending_json TEXT,
                  imported_v3 INTEGER NOT NULL DEFAULT 0,
                  source_snapshot TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS v3_shift_selection (
                  shift_id INTEGER PRIMARY KEY,
                  technicians_json TEXT NOT NULL DEFAULT '[]',
                  filename TEXT NOT NULL DEFAULT '',
                  imported_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS v3_shift_source (
                  shift_id INTEGER PRIMARY KEY, filename TEXT NOT NULL, rows_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v3_vendors (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, shift_id INTEGER NOT NULL,
                  name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
                  created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS v3_pm_tracking (
                  shift_id INTEGER NOT NULL, wo_number TEXT NOT NULL,
                  was_preexisting INTEGER NOT NULL DEFAULT 0,
                  PRIMARY KEY (shift_id,wo_number)
                );
            """)
            cols={row[1] for row in c.execute("PRAGMA table_info(v3_entry_meta)")}
            if "rfr_type" not in cols:
                c.execute("ALTER TABLE v3_entry_meta ADD COLUMN rfr_type TEXT NOT NULL DEFAULT ''")
            if "selection_hidden" not in cols:
                c.execute("ALTER TABLE v3_entry_meta ADD COLUMN selection_hidden INTEGER NOT NULL DEFAULT 0")
            if "source_snapshot" not in cols:
                c.execute("ALTER TABLE v3_entry_meta ADD COLUMN source_snapshot TEXT NOT NULL DEFAULT ''")
            batch_cols={row[1] for row in c.execute("PRAGMA table_info(v3_import_batches)")}
            if "parse_stats_json" not in batch_cols:
                # Import diagnostics (2026-10-24 facility request): source row
                # count, skip reasons, parsed count -- captured at stage time
                # since only stage_import() has the raw file bytes; confirm_import()
                # reads it back to build the full diagnostics notice. Nullable/
                # defaulted so every existing direct-INSERT test call site
                # (which lists explicit columns, omitting this one) keeps working.
                c.execute("ALTER TABLE v3_import_batches ADD COLUMN parse_stats_json TEXT NOT NULL DEFAULT '{}'")

    def secure(request, shift_id=None):
        if get_mst_roster is not None:
            who=decode_session(request)
            if not who: raise HTTPException(401,"MST login required")
            _v3_live_shifts()
            shift=db.get_shift(shift_id or who["shift_id"]) if (shift_id or who["shift_id"]) else None
            if not shift or not shift.get("is_active"): raise HTTPException(404,"Choose your active shift")
            if not shift_owned_by(shift,who) or who["shift_id"] != shift["id"]:
                raise HTTPException(403,"This session is bound to another shift")
        else:
            if not is_lead_authed(request): raise HTTPException(401,"MST login required")
            shift=db.get_shift(shift_id) if shift_id else resolve_my_shift(request)
            if not shift or not shift.get("is_active"): raise HTTPException(404,"No active shift")
            if not can_manage_shift(request,shift["id"]): raise HTTPException(403,"You do not own this shift")
        ensure_tables()
        return shift

    def model(shift_id):
        entries = db.get_entries(shift_id)
        wo_causes = db.get_wo_causes(shift_id)
        with db._conn() as c:
            meta_rows = c.execute("SELECT * FROM v3_entry_meta WHERE shift_id=?", (shift_id,)).fetchall()
            pm_rows = c.execute("SELECT * FROM pm_wos WHERE shift_id=? ORDER BY wo_number", (shift_id,)).fetchall()
            selection = c.execute("SELECT * FROM v3_shift_selection WHERE shift_id=?", (shift_id,)).fetchone()
        meta = {m["entry_id"]: dict(m) for m in meta_rows}
        groups = {}
        for e in entries:
            # Include existing manual/V2 entries as well as newer V3 Cetaris entries.
            key = e.get("wo_number") or f"manual:{e['id']}"
            m = meta.get(e["id"], {})
            r = dict(e)
            r["reported_issue"] = m.get("reported_issue") or e.get("issue", "")
            # Cause text: an edited/imported cause on the entry itself wins; otherwise
            # fall back to a separately-uploaded cause/repair-description export
            # (same two-file workflow as V2 -- see cause_lookup.py), joined live by
            # WO Number so upload order (timecard first or cause file first) never matters.
            r["cause_text"] = m.get("cause_text") or wo_causes.get(e.get("wo_number") or "", "")
            r["needs_review"] = bool(m.get("needs_review"))
            r["mst_edited"] = bool(m.get("mst_edited"))
            r["rfr_type"] = m.get("rfr_type") or ("Work Request" if e.get("tech_name") in ("MST (Manual)","Shift Owner (Manual)") else "Other")
            r["remarks_list"] = _remarks(e)
            g = groups.setdefault(key, {
                "key": key, "wo_number": e.get("wo_number") or "", "main_id":e["id"],
                "unit":e.get("unit") or "", "reported_issue":r["reported_issue"],
                "cause_text":r["cause_text"], "rfr_type":r["rfr_type"], "rows":[], "excluded":True,"needs_review":False,
            })
            g["rows"].append(r)
            if not e.get("excluded"):
                g["excluded"] = False
            if r["needs_review"]:
                g["needs_review"] = True
            if not g["cause_text"] and r["cause_text"]:
                g["cause_text"] = r["cause_text"]
        return {
            # Excluded happenings (deselected technician, stale WO, manual
            # exclude) are noise, not signal -- the shift owner only needs to
            # review and report on what's actually in scope for this shift.
            "happenings": [g for g in groups.values() if not g["excluded"]],
            "pm_wos": [dict(p) for p in pm_rows],
            "pm_count": len(pm_rows),
            "selected_techs": json.loads(selection["technicians_json"]) if selection else [],
            "imported_filename": selection["filename"] if selection else "",
            "imported_at": selection["imported_at"] if selection else "",
        }

    def _remarks(e):
        try:
            value = json.loads(e.get("raw_remarks") or "[]")
            if isinstance(value, list):
                return [str(v).strip() for v in value if str(v).strip()]
        except (TypeError, ValueError):
            pass
        return [str(e.get("action") or "").strip()] if e.get("action") else []

    def group_for_entry(shift_id, entry_id):
        e = db.get_entry(entry_id)
        if not e or e["shift_id"] != shift_id:
            raise HTTPException(404, "Happening not found on this shift")
        if e.get("wo_number"):
            return [r for r in db.get_entries(shift_id) if r.get("wo_number") == e["wo_number"]]
        return [e]

    def v3_vendors(shift_id):
        ensure_tables()
        with db._conn() as c:
            return [dict(row) for row in c.execute(
                "SELECT id,name,notes FROM v3_vendors WHERE shift_id=? ORDER BY id",(shift_id,))]

    def return_workspace(shift_id, message):
        from urllib.parse import urlencode
        return RedirectResponse("/v3?"+urlencode({"shift_id":shift_id,"notice":message}),status_code=303)

    @router.post("/oos/add")
    async def v3_oos_add(request:Request,shift_id:int=Form(...),unit:str=Form(...)):
        secure(request,shift_id)
        unit=unit.strip().upper()
        if not unit or len(unit)>80: raise HTTPException(400,"Asset ID must be 1-80 characters")
        db.add_oos_unit(unit)
        return return_workspace(shift_id,"Asset marked out of service")

    @router.post("/oos/rts")
    async def v3_oos_rts(request:Request,shift_id:int=Form(...),unit:str=Form(...)):
        secure(request,shift_id)
        unit=unit.strip()
        if unit not in db.get_oos_units(): raise HTTPException(409,"Asset is not currently OOS")
        # Both changes happen in the same database transaction.
        with db._conn() as c:
            c.execute("INSERT INTO rts_log (shift_id,unit,removed_at) VALUES (?,?,?)",
                      (shift_id,unit,datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")))
            c.execute("DELETE FROM oos_units WHERE unit=?",(unit,))
        return return_workspace(shift_id,"Asset returned to service")

    @router.post("/breakdown/decide")
    async def v3_breakdown_decide(request:Request,shift_id:int=Form(...),wo_number:str=Form(...),decision:str=Form(...)):
        secure(request,shift_id)
        if decision not in ("rts","keep_oos"): raise HTTPException(400,"Decision must be rts or keep_oos")
        v3_breakdown.decide(db,shift_id,{wo_number:decision})
        notice="Asset returned to service" if decision=="rts" else "Asset kept out of service per MST decision"
        return return_workspace(shift_id,notice)

    @router.post("/import-review/{review_id}/include")
    async def v3_import_review_include(request:Request,review_id:int,shift_id:int=Form(...)):
        secure(request,shift_id)
        if not v3_import_review.include(db,shift_id,review_id):
            raise HTTPException(404,"Review item not found or already resolved")
        return return_workspace(shift_id,"Work order included in this shift")

    @router.post("/import-review/{review_id}/dismiss")
    async def v3_import_review_dismiss(request:Request,review_id:int,shift_id:int=Form(...)):
        secure(request,shift_id)
        if not v3_import_review.dismiss(db,shift_id,review_id):
            raise HTTPException(404,"Review item not found or already resolved")
        return return_workspace(shift_id,"Work order dismissed from review")

    @router.post("/import-review/{review_id}/assign-shift")
    async def v3_import_review_assign_shift(request:Request,review_id:int,shift_id:int=Form(...),shift_type:str=Form(...)):
        secure(request,shift_id)
        if not v3_import_review.assign_shift(db,shift_id,review_id,shift_type.strip().upper()):
            raise HTTPException(404,"Review item not found, already resolved, or invalid shift")
        return return_workspace(shift_id,f"Ownership assigned to {shift_type.strip().upper()}; honored on the next import of this WO")

    @router.post("/rts/undo")
    async def v3_undo_rts(request:Request,shift_id:int=Form(...),unit:str=Form(...)):
        secure(request,shift_id)
        if unit not in db.get_rts_events(shift_id): raise HTTPException(404,"RTS record not found")
        db.delete_rts_event(shift_id,unit)
        return return_workspace(shift_id,"RTS log entry removed")

    @router.post("/vendor/add")
    async def v3_vendor_add(request:Request,shift_id:int=Form(...),name:str=Form(...),notes:str=Form("")):
        secure(request,shift_id)
        name=name.strip();notes=notes.strip()
        if not name or len(name)>160 or len(notes)>2000: raise HTTPException(400,"Invalid vendor details")
        ensure_tables()
        with db._conn() as c:
            c.execute("INSERT INTO v3_vendors (shift_id,name,notes,created_at) VALUES (?,?,?,?)",
                      (shift_id,name,notes,datetime.now(timezone.utc).isoformat()))
        return return_workspace(shift_id,"Vendor saved")

    @router.post("/vendor/{vendor_id}/delete")
    async def v3_vendor_delete(request:Request,vendor_id:int,shift_id:int=Form(...)):
        secure(request,shift_id)
        with db._conn() as c:
            c.execute("DELETE FROM v3_vendors WHERE id=? AND shift_id=?",(vendor_id,shift_id))
        return return_workspace(shift_id,"Vendor removed")

    @router.post("/carry-over/{entry_id}/adopt")
    async def v3_carry_adopt(request:Request,entry_id:int,shift_id:int=Form(...)):
        shift=secure(request,shift_id)
        source=db.get_entry(entry_id)
        if not source or not source.get("carry_over") or source["shift_id"]>=shift_id:
            raise HTTPException(404,"Previous-shift carry-over not found")
        if entry_id not in {e["id"] for e in db.get_carry_over_entries(shift_id)}:
            raise HTTPException(409,"Carry-over no longer available")
        with db._conn() as c:
            c.execute("INSERT INTO entries (shift_id,tech_name,unit,issue,action,result,priority,created_at) "
                      "VALUES (?,?,?,?,?,?,?,?)",
                      (shift_id,str(shift.get("lead_name") or "Shift owner")+" (adopted)",
                       source["unit"],source["issue"],source.get("action", ""),
                       source.get("result", ""),source.get("priority", "monitor"),datetime.now(timezone.utc).isoformat()))
            c.execute("UPDATE entries SET carry_over=0 WHERE id=? AND carry_over=1",(entry_id,))
        return return_workspace(shift_id,"Carry-over adopted")

    @router.post("/carry-over/{entry_id}/dismiss")
    async def v3_carry_dismiss(request:Request,entry_id:int,shift_id:int=Form(...)):
        secure(request,shift_id)
        if entry_id not in {e["id"] for e in db.get_carry_over_entries(shift_id)}:
            raise HTTPException(404,"Previous-shift carry-over not found")
        db.set_carry_over(entry_id,False)
        return return_workspace(shift_id,"Carry-over dismissed")

    def render(request, shift, **extra):
        info = model(shift["id"])
        context = {
            "shift":shift,
            **info,
            "loto":db.get_loto(shift["id"]),
            "oos_units":db.get_oos_units(),
            "rts_units":db.get_rts_events(shift["id"]),
            "breakdown_pending":v3_breakdown.pending(db),
            "import_review":v3_import_review.pending(db,shift["id"]),
            "shift_notes":db.get_shift_notes(shift["id"]),
            "carry_overs":db.get_carry_over_entries(shift["id"]),
            "vendors":v3_vendors(shift["id"]),
            "notice":request.query_params.get("notice", ""),
            "warning":request.query_params.get("warning", ""),
            **extra,
        }
        res=templates.TemplateResponse(request,"v3/workspace.html",context)
        res.headers["Cache-Control"]="no-store"
        return res

    @router.get("", response_class=HTMLResponse)
    async def workspace(request:Request, shift_id:int=0):
        if get_mst_roster is not None:
            if not decode_session(request): return RedirectResponse("/v3/login",status_code=303)
        elif not is_lead_authed(request):
            return lead_redirect("/v3")
        try:
            shift=secure(request, shift_id or None)
        except HTTPException as exc:
            if exc.status_code in (403,404):
                return RedirectResponse("/v3/choose", status_code=303)
            raise
        return render(request,shift)

    @router.get("/choose", response_class=HTMLResponse)
    async def choose_shift(request:Request):
        if get_mst_roster is not None:
            who=decode_session(request)
            if not who: return RedirectResponse("/v3/login",status_code=303)
            available=accessible_shifts(request)
        else:
            if not is_lead_authed(request): return lead_redirect("/v3/choose")
            available=[s for s in db.get_active_shifts() if can_manage_shift(request,s["id"])]
        return templates.TemplateResponse(request,"v3/choose.html",{"shifts":available,
            "today":(now_func() if now_func else datetime.now()).date().isoformat(),
            "allow_start":get_mst_roster is not None})

    @router.get("/history", response_class=HTMLResponse)
    async def v3_history(request:Request, shift_id:int=0):
        # History is read-only; require an authenticated V3 session.
        if get_mst_roster is not None:
            if not decode_session(request):
                return RedirectResponse("/v3/login",status_code=303)
        elif not is_lead_authed(request):
            return lead_redirect("/v3/history")
        return templates.TemplateResponse(request,"v3/history.html",{
            "shift_id":shift_id,
            "shifts":db.list_past_shifts_with_counts(limit=100),
        })

    @router.get("/history/{shift_id}/eml")
    async def v3_history_eml(request:Request, shift_id:int):
        """Re-download the last generated report for any past shift as an
        Outlook .eml, same compose format as the live Generate & Send flow --
        does not regenerate the report, just re-packages what was already sent."""
        if get_mst_roster is not None:
            if not decode_session(request):
                return RedirectResponse("/v3/login",status_code=303)
        elif not is_lead_authed(request):
            return lead_redirect("/v3/history")
        if build_eml_response is None:
            raise HTTPException(503,"Email composer unavailable")
        report=db.get_last_report_for_shift(shift_id)
        if not report:
            raise HTTPException(404,"No report has been generated for this shift yet")
        return await build_eml_response(report["id"],subject="",html_body="")

    @router.get("/analytics", response_class=HTMLResponse)
    async def v3_analytics(request:Request, shift_id:int=0):
        if get_mst_roster is not None:
            if not decode_session(request):
                return RedirectResponse("/v3/login",status_code=303)
        elif not is_lead_authed(request):
            return lead_redirect("/v3/analytics")
        data=db.get_analytics()
        return templates.TemplateResponse(request,"v3/analytics.html",{
            "shift_id":shift_id,
            "data":data,
            "data_json":json.dumps(data),
        })

    def _admin_authed(request:Request) -> bool:
        """Matches V2's gate: any authenticated session can view/configure
        admin. Manager-only sub-sections would check is_manager on top of
        this, the same way V2's admin.html does for OT codes."""
        if get_mst_roster is not None:
            return bool(decode_session(request))
        return bool(is_lead_authed(request))

    @router.get("/admin", response_class=HTMLResponse)
    async def v3_admin(request:Request):
        if not _admin_authed(request):
            if get_mst_roster is not None:
                return RedirectResponse("/v3/login",status_code=303)
            return lead_redirect("/v3/admin")
        who=decode_session(request)
        is_manager=bool(who["profile"].get("is_manager")) if who else True
        return templates.TemplateResponse(request,"v3/admin.html",{
            "dc_number":db.get_setting("dc_number","IND3"),
            "email_to":db.get_setting("email_to",""),
            "email_from_name":db.get_setting("email_from_name","IND3 Shift Passdown"),
            "ms_client_id":db.get_setting("ms_client_id",""),
            "teams_webhook_url":db.get_setting("teams_webhook_url",""),
            "roster_count":len(get_mst_roster() if get_mst_roster else {}),
            "is_manager":is_manager,
            "notice":request.query_params.get("notice",""),
        })

    @router.post("/admin/settings")
    async def v3_admin_settings(request:Request,dc_number:str=Form(...),
                                email_to:str=Form(""),email_from_name:str=Form(""),
                                ms_client_id:str=Form("")):
        who=decode_session(request)
        if get_mst_roster is not None and not who:
            return RedirectResponse("/v3/login",status_code=303)
        if get_mst_roster is None and not is_lead_authed(request):
            raise HTTPException(403,"Management access required")
        dc_number=dc_number.strip().upper()
        if not dc_number or len(dc_number)>20:
            raise HTTPException(400,"Invalid distribution center")
        db.set_setting("dc_number",dc_number)
        db.set_setting("email_to",email_to.strip()[:2000])
        db.set_setting("email_from_name",email_from_name.strip()[:160] or "IND3 Shift Passdown")
        db.set_setting("ms_client_id",ms_client_id.strip()[:300])
        return RedirectResponse("/v3/admin?notice=Configuration+saved",status_code=303)

    def _refresh_roster():
        if refresh_roster:
            refresh_roster()

    def _roster_list_response(request:Request):
        return templates.TemplateResponse(request,"partials/v3_assoc_list.html",{"associates":db.get_associates()})

    @router.get("/admin/roster", response_class=HTMLResponse)
    async def v3_roster_list(request:Request):
        if not _admin_authed(request):
            return HTMLResponse("",status_code=403)
        return _roster_list_response(request)

    @router.post("/admin/roster/reload", response_class=HTMLResponse)
    async def v3_roster_reload(request:Request):
        if not _admin_authed(request):
            return HTMLResponse("",status_code=403)
        _refresh_roster()
        return HTMLResponse('<span class="v3-roster-reload-ok">Roster re-synced.</span>')

    @router.post("/admin/roster/add", response_class=HTMLResponse)
    async def v3_roster_add(request:Request,assoc_num:str=Form(...),full_name:str=Form(...),
                            shift:str=Form(""),default_id:str=Form(""),is_mst:str=Form("0")):
        if not _admin_authed(request):
            return HTMLResponse("",status_code=403)
        if not assoc_num.strip() or not full_name.strip():
            return HTMLResponse('<p class="v3-roster-error">Associate # and Name are required.</p>')
        db.add_associate(assoc_num.strip(),full_name.strip(),shift.strip(),default_id.strip(),is_mst=(is_mst=="1"))
        _refresh_roster()
        return _roster_list_response(request)

    @router.post("/admin/roster/{assoc_num}/toggle-mst", response_class=HTMLResponse)
    async def v3_roster_toggle_mst(request:Request,assoc_num:str):
        if not _admin_authed(request):
            return HTMLResponse("",status_code=403)
        assoc=db.get_associate(assoc_num)
        if not assoc:
            raise HTTPException(404)
        db.set_associate_mst(assoc_num,not bool(assoc.get("is_mst",0)))
        _refresh_roster()
        return _roster_list_response(request)

    @router.get("/admin/roster/{assoc_num}/edit-form", response_class=HTMLResponse)
    async def v3_roster_edit_form(request:Request,assoc_num:str):
        if not _admin_authed(request):
            return HTMLResponse("",status_code=403)
        assoc=db.get_associate(assoc_num)
        if not assoc:
            raise HTTPException(404)
        return templates.TemplateResponse(request,"partials/v3_assoc_row_edit.html",{"a":assoc})

    @router.get("/admin/roster/{assoc_num}/edit-form/cancel", response_class=HTMLResponse)
    async def v3_roster_edit_cancel(request:Request,assoc_num:str):
        if not _admin_authed(request):
            return HTMLResponse("",status_code=403)
        assoc=db.get_associate(assoc_num)
        if not assoc:
            raise HTTPException(404)
        return templates.TemplateResponse(request,"partials/v3_assoc_row.html",{"a":assoc})

    @router.post("/admin/roster/{assoc_num}/edit", response_class=HTMLResponse)
    async def v3_roster_edit_save(request:Request,assoc_num:str,new_assoc_num:str=Form(...),
                                  full_name:str=Form(...),shift:str=Form(""),is_mst:str=Form("0")):
        if not _admin_authed(request):
            return HTMLResponse("",status_code=403)
        if not new_assoc_num.strip() or not full_name.strip():
            return HTMLResponse('<p class="v3-roster-error">Associate # and Name are required.</p>')
        db.update_associate(assoc_num,new_assoc_num.strip(),full_name.strip(),shift.strip(),is_mst=(is_mst=="1"))
        _refresh_roster()
        return _roster_list_response(request)

    @router.delete("/admin/roster/{assoc_num}", response_class=HTMLResponse)
    async def v3_roster_delete(request:Request,assoc_num:str):
        if not _admin_authed(request):
            return HTMLResponse("",status_code=403)
        db.delete_associate(assoc_num)
        _refresh_roster()
        return _roster_list_response(request)

    @router.get("/operations", response_class=HTMLResponse)
    async def v3_operations(request:Request, shift_id:int):
        shift=secure(request,shift_id)
        return templates.TemplateResponse(request,"v3/operations.html",{
            "shift":shift,
            "report":db.get_last_report_for_shift(shift_id),
            "warning":request.query_params.get("warning",""),
        })

    @router.post("/generate")
    async def v3_generate(request:Request,shift_id:int=Form(...)):
        secure(request,shift_id)
        if generate_report is None:
            raise HTTPException(503,"Report generator unavailable")
        # Reuse the established report-generation engine without rendering V2 HTML.
        # NOTE: lead_generate's other parameters are typed as `= Form(default=[])`,
        # which only resolves to a real list when FastAPI itself parses the request.
        # Called directly like this (not as a route), those defaults stay unresolved
        # Form marker objects -- must pass real empty lists explicitly or it crashes
        # the moment lead_generate tries to iterate them (e.g. rts_unit).
        await generate_report(request,shift_id=shift_id,extra_title=[],extra_note=[],
                              vendor_name=[],ro_item=[],rts_unit=[])
        report=db.get_last_report_for_shift(shift_id)
        if not report:
            raise HTTPException(500,"Report was not saved")
        return RedirectResponse(f"/v3/report/{report['id']}?shift_id={shift_id}",status_code=303)

    @router.get("/send")
    async def v3_send(request:Request,shift_id:int):
        """One-click 'Generate & Send': builds the report, then streams the .eml
        directly back as a download -- same single-hop UX as V2's compose button,
        no intermediate operations/report pages."""
        secure(request,shift_id)
        if generate_report is None:
            raise HTTPException(503,"Report generator unavailable")
        if build_eml_response is None:
            raise HTTPException(503,"Email composer unavailable")
        await generate_report(request,shift_id=shift_id,extra_title=[],extra_note=[],
                              vendor_name=[],ro_item=[],rts_unit=[])
        report=db.get_last_report_for_shift(shift_id)
        if not report:
            raise HTTPException(500,"Report was not saved")
        return await build_eml_response(report["id"],subject="",html_body="")

    def report_access(request,report_id):
        report=db.get_report(report_id)
        if not report: raise HTTPException(404,"Report not found")
        shift=db.get_shift(report["shift_id"])
        if not shift: raise HTTPException(404,"Shift not found")
        if get_mst_roster is not None:
            who=decode_session(request)
            if not who: raise HTTPException(401,"Sign in to view report")
            if not who["profile"].get("is_manager"):
                opener=(shift.get("opened_by_assoc") or "").strip()
                if opener != who["associate"] or who["shift_id"] != shift["id"]:
                    raise HTTPException(403,"Report belongs to another shift owner")
        elif not is_lead_authed(request):
            raise HTTPException(401,"Sign in to view report")
        return shift,report

    @router.get("/report/{report_id}", response_class=HTMLResponse)
    async def v3_report(request:Request,report_id:int,shift_id:int=0):
        shift,report=report_access(request,report_id)
        return templates.TemplateResponse(request,"v3/report.html",{"shift":shift,"report":report})

    @router.post("/end")
    async def v3_end(request:Request,shift_id:int=Form(...)):
        shift=secure(request,shift_id)
        report=db.get_last_report_for_shift(shift_id)
        if not report:
            return RedirectResponse(f"/v3/operations?shift_id={shift_id}&warning=Generate+a+report+before+ending+the+shift",status_code=303)
        db.close_shift(shift_id)
        return RedirectResponse(f"/v3/ended?shift_id={shift_id}",status_code=303)

    @router.get("/ended",response_class=HTMLResponse)
    async def v3_ended(request:Request,shift_id:int):
        shift=db.get_shift(shift_id)
        if not shift or shift.get("is_active"):
            raise HTTPException(404,"Closed shift not found")
        report=db.get_last_report_for_shift(shift_id)
        if not report:
            raise HTTPException(404,"No saved report for shift")
        report_access(request,report["id"])
        return templates.TemplateResponse(request,"v3/ended.html",{"shift":shift,"report":report})

    @router.get("/technicians",response_class=HTMLResponse)
    async def technicians(request:Request,shift_id:int):
        shift=secure(request,shift_id)
        with db._conn() as c:
            source=c.execute("SELECT * FROM v3_shift_source WHERE shift_id=?",(shift_id,)).fetchone()
        if not source: return RedirectResponse(f"/v3?shift_id={shift_id}&warning=Upload+the+Cetaris+file+first",status_code=303)
        records=json.loads(source["rows_json"])
        token=uuid.uuid4().hex
        with db._conn() as c:
            c.execute("INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)",
                      (token,shift_id,source["filename"],datetime.now().isoformat(timespec="seconds"),source["rows_json"]))
        return render(request,shift,staged={"token":token,"filename":source["filename"],
                                           "counts":technician_counts(records),"total_rows":len(records)})

    def _name_key(name):
        import re as _re
        return " ".join(sorted(_re.sub(r"[,]", " ", name or "").lower().split()))

    def _roster_home_shift_map(tech_names):
        """Map each Cetaris tech_name to their roster home shift (S1-S5), for
        v3_split.assign()'s overlap tie-breaking. Matches by normalized name
        key so 'Last, First' (Cetaris) resolves against 'First Last' (roster).
        """
        if not get_mst_roster:
            return {}
        roster=get_mst_roster()
        by_key={}
        for profile in roster.values():
            home=str(profile.get("shift") or "").strip().upper()
            if home not in ("S1","S2","S3","S4","S5"):
                continue
            for label in (profile.get("full_name"),profile.get("display_name")):
                if label:
                    by_key[_name_key(label)]=home
        return {name:by_key[_name_key(name)] for name in tech_names if _name_key(name) in by_key}

    def _roster_name_keys_for_shift(shift_type):
        """Normalized full-name keys for every roster associate assigned to this
        shift code, so a Cetaris 'Last, First' name can match a roster record
        stored 'FIRST LAST' without requiring an exact-format match."""
        if not get_mst_roster or not shift_type:
            return set()
        roster=get_mst_roster()
        return {
            _name_key(p.get("full_name") or p.get("display_name") or "")
            for p in roster.values()
            if str(p.get("shift") or "").upper()==shift_type and not p.get("is_manager")
        }

    def _auto_checked_techs(shift, tech_names, previously_selected):
        """Pre-check technicians already on this shift's roster, plus anyone
        previously confirmed on this shift -- the MST only has to additionally
        check overtime/other-shift associates who showed up, not re-pick
        their own crew every single upload."""
        roster_keys=_roster_name_keys_for_shift(shift.get("shift_type"))
        checked={name for name in tech_names if _name_key(name) in roster_keys}
        checked |= (set(previously_selected) & set(tech_names))
        return checked

    @router.post("/import",response_class=HTMLResponse)
    async def stage_import(request:Request,shift_id:int=Form(...),file:UploadFile|None=File(default=None)):
        shift=secure(request,shift_id)
        if file is None or not file.filename or not file.filename.strip():
            return render(request,shift,warning="No file was selected. Choose your Cetaris work order export and try again.")
        data=await file.read(MAX_SIZE + 1)
        # Cause/repair-description export (WO Number + Repair Description) --
        # a separate file from the timecard export, same as V2. Detected by
        # header shape so it never gets routed through parse_excel(). Just
        # joins by WO Number and stores the text; doesn't touch entries.
        if cause_lookup.looks_like_cause_file(data):
            causes=cause_lookup.parse_cause_file(data)
            for wo_number,cause_text in causes.items():
                db.upsert_wo_cause(shift_id=shift_id,wo_number=wo_number,cause_text=cause_text)
            return render(request,shift,notice=f"Linked cause text for {len(causes)} work order(s).")
        # No filename/extension gate here on purpose: the exact device or
        # browser a tech exports from (laptop, workstation, etc.) can produce
        # odd filenames (missing/duplicated extensions, re-saved copies).
        # parse_excel() below validates by actual CONTENT -- it opens the
        # file as a real workbook and checks for the expected columns -- so
        # rejection is based on whether the data is a real Cetaris export,
        # never on what the filename happens to look like. Universal by
        # construction, not by guessing which extensions to allow-list.
        try:
            parse_stats={}
            records=parse_excel(data,stats=parse_stats)
        except ValueError as exc:
            return render(request,shift,warning=str(exc))
        if not records:
            return render(request,shift,warning="No technician labor rows found in this report.")
        token=uuid.uuid4().hex
        with db._conn() as c:
            # Old unconfirmed imports are staging data, not shift history.
            c.execute("DELETE FROM v3_import_batches WHERE created_at < ?", ((datetime.now()-timedelta(days=1)).isoformat(timespec="seconds"),))
        with db._conn() as c:
            c.execute("INSERT INTO v3_import_batches (token,shift_id,filename,created_at,rows_json,parse_stats_json) VALUES (?,?,?,?,?,?)",
                    (token,shift_id,file.filename[:240],datetime.now().isoformat(timespec="seconds"),json.dumps(records),json.dumps(parse_stats)))
        info=model(shift_id)
        tech_names=[c["name"] for c in technician_counts(records)]
        auto_checked=_auto_checked_techs(shift,tech_names,info["selected_techs"])
        return render(request,shift,staged={
            "token":token,"filename":file.filename,"counts":technician_counts(records),
            "total_rows":len(records),"auto_checked":sorted(auto_checked),
        })

    @router.post("/import/{token}/confirm")
    async def confirm_import(request:Request,token:str,shift_id:int=Form(...),techs:list[str]=Form(default=[])):
        shift=secure(request,shift_id)
        with db._conn() as c:
            batch=c.execute("SELECT * FROM v3_import_batches WHERE token=? AND shift_id=?",(token,shift_id)).fetchone()
        if not batch:
            raise HTTPException(404,"This import expired or does not belong to your shift")
        try:
            staged_at=datetime.fromisoformat(batch["created_at"])
        except (TypeError,ValueError):
            raise HTTPException(400,"Invalid import staging timestamp")
        if datetime.now()-staged_at > timedelta(hours=24):
            raise HTTPException(410,"Import preview expired. Upload a fresh Cetaris report.")
        rows=json.loads(batch["rows_json"])
        try:
            parse_stats=json.loads(batch["parse_stats_json"] or "{}")
        except (TypeError,ValueError):
            parse_stats={}
        allowed={r["tech_name"] for r in rows}
        selected=set(techs) & allowed
        if not selected:
            return render(request,shift,warning="Select at least one technician before extracting.",staged={
                "token":token,"filename":batch["filename"],"counts":technician_counts(rows),"total_rows":len(rows)})
        window=shift_window(shift["shift_type"],date.fromisoformat(shift["date"]))
        if not window:
            return render(request,shift,warning="Shift timing is not configured. Import cannot continue.")

        # Every shift (S1-S5, including Thursday S3 carried into Friday
        # morning, and the S4/S5 Friday-evening overlap) uses the same WO
        # ownership engine: technician work-start evidence first, roster home
        # shift and technician continuity to resolve overlaps, equipment-return
        # evidence (In Service / Update Date) as a fallback, and an explicit
        # MST shift choice when evidence is missing or genuinely conflicting.
        # A later administrative WO Closed Date/Time never transfers ownership.
        split_pending=[]
        submitted=await request.form()
        decisions={key[len("split_wo_"):]:value for key,value in submitted.multi_items()
                   if key.startswith("split_wo_") and value in ("S1","S2","S3","S4","S5")}
        # Standing MST ownership calls apply here too -- a decision made once
        # (by any shift) is honored on every later re-upload of that WO,
        # never re-asked, and this form's own explicit choice wins if both
        # are present in the same submission.
        decisions={**v3_import_review.ownership_decisions(db),**decisions}
        roster_shifts=_roster_home_shift_map(allowed)
        # S5's 06:00-06:30 morning grace is resolved by v3_split.py as just
        # another scheduled overlap (roster home shift, then the technician's
        # own earlier unambiguous S5 work-start that same night) -- technician
        # SELECTION for S5 only confirms shift participation, never grants
        # automatic ownership of grace-window work on its own.
        all_rows=rows
        rows,split_pending,owned_elsewhere=v3_split.assign(rows,selected,shift["shift_type"],date.fromisoformat(shift["date"]),
                                           decisions,roster_shifts=roster_shifts)
        if split_pending:
            v3_import_review.record(db,shift_id,{"ownership":split_pending})

        # Breakdown WOs get sidecar OOS/RTS lifecycle tracking independent of
        # the happenings/PM report -- runs on every import, every shift type.
        # Shares the existing oos_units/rts_log tables, so a WO going "in
        # process" shows up in the OOS panel you already have.
        # allowed_new_wos restricts tracking to WOs the ownership engine just
        # attributed to THIS shift -- a breakdown opened by another shift must
        # never start being tracked here just because its technician also
        # appears in this file. all_rows (pre-attribution) is still what gets
        # scanned, so a STATUS UPDATE to an already-tracked WO (e.g. someone
        # closing out a breakdown that belongs to a different shift) still
        # flows through -- OOS/RTS state is shared infrastructure, not
        # shift-exclusive; only *starting* new tracking is ownership-gated.
        breakdown_updates=v3_breakdown.sync(db,all_rows,selected,shift_id,
                                            allowed_new_wos={r["wo_number"] for r in rows})
        records,stats=select_records(rows,selected,*window,trust_attribution=True)
        # Cross-shift completion credit must be unique across the database --
        # the ownership engine above already decides which ONE shift a WO
        # belongs to, but a stale concurrent upload on another shift could
        # still have beaten this one to the punch. Block rather than silently
        # double-credit or overwrite the other shift's record.
        completed_wos={r["wo_number"] for r in records if r["kind"] in ("pm","happening")}
        cross_shift=[]
        if completed_wos:
            placeholders=",".join("?" for _ in completed_wos)
            with db._conn() as c:
                cross_shift=c.execute(f"""
                    SELECT DISTINCT wo_number,shift_id FROM pm_wos
                    WHERE shift_id<>? AND wo_number IN ({placeholders})
                    UNION
                    SELECT DISTINCT wo_number,shift_id FROM entries
                    WHERE shift_id<>? AND excluded=0 AND wo_number IN ({placeholders})
                """,(shift_id,*sorted(completed_wos),shift_id,*sorted(completed_wos))).fetchall()
        if cross_shift:
            labels=", ".join(f"WO {r['wo_number']} (shift #{r['shift_id']})" for r in cross_shift[:8])
            return render(request,shift,
                warning="Import blocked to prevent double-credit across shifts: "+labels+
                        (f" and {len(cross_shift)-8} more" if len(cross_shift)>8 else "")+
                        ". Review the existing shift's WO before retrying.",
                staged={"token":token,"filename":batch["filename"],
                        "counts":technician_counts(rows),"total_rows":len(rows)})
        # Persist WHICH WOs were excluded/ambiguous, with their close time and
        # reason, not just an aggregate count -- an MST couldn't previously
        # see or act on anything beyond "N skipped". Hits overnight shifts
        # (S3, S5) hardest since midnight-crossing timestamps are the most
        # likely to disagree with a shift's computed window.
        v3_import_review.record(db,shift_id,{"ambiguous":stats.get("ambiguous",[]),"excluded":stats.get("excluded",[])})
        added,unchanged,pending=0,0,0
        # Use short transactions. Existing db helpers open their own connections;
        # nesting them inside an active SQLite write transaction risks 'database locked'.
        with db._conn() as c:
            previous=c.execute("SELECT technicians_json FROM v3_shift_selection WHERE shift_id=?",(shift_id,)).fetchone()
        old_selected=set(json.loads(previous[0])) if previous else set()
        # Only reconsider technicians who actually appear in THIS upload's rows.
        # A tech absent from a narrower/later re-export (timing, partial export,
        # etc.) must never have their already-confirmed entries silently mass-
        # excluded just because this particular file doesn't mention them --
        # only an explicit uncheck of someone who IS listed here means "remove".
        removed=(old_selected & allowed)-selected
        # Only auto-exclude a technician's V3 imports, preserving prior manual exclusions.
        if removed:
            placeholders=",".join("?" for _ in removed)
            with db._conn() as c:
                c.execute(f"""UPDATE v3_entry_meta SET selection_hidden=1
                    WHERE imported_v3=1 AND shift_id=?
                    AND entry_id IN (SELECT id FROM entries WHERE shift_id=?
                        AND tech_name IN ({placeholders}) AND excluded=0)""",
                    (shift_id,shift_id,*sorted(removed)))
                c.execute(f"""UPDATE entries SET excluded=1 WHERE shift_id=?
                    AND tech_name IN ({placeholders})
                    AND id IN (SELECT entry_id FROM v3_entry_meta WHERE imported_v3=1)""",
                    (shift_id,*sorted(removed)))
        # Restore only entries auto-hidden by the technician selection (not MST exclusions).
        if selected:
            placeholders=",".join("?" for _ in selected)
            with db._conn() as c:
                c.execute(f"""UPDATE entries SET excluded=0 WHERE shift_id=?
                    AND tech_name IN ({placeholders})
                    AND id IN (SELECT entry_id FROM v3_entry_meta WHERE selection_hidden=1)""",
                    (shift_id,*sorted(selected)))
                c.execute(f"""UPDATE v3_entry_meta SET selection_hidden=0
                    WHERE shift_id=? AND entry_id IN (SELECT id FROM entries
                        WHERE shift_id=? AND tech_name IN ({placeholders}))""",
                    (shift_id,shift_id,*sorted(selected)))
        # A fresh Cetaris export can reopen a WO, change it into a PM, or drop
        # it from this shift. Keep its source/edits for audit, but do not report
        # stale imported happenings as completed. Only V3-imported entries are
        # affected; manual entries and original V2 records remain untouched.
        current_keys={(r["wo_number"],r["tech_name"]) for r in records if r["kind"]=="happening"}
        with db._conn() as c:
            previous_imports=c.execute("""SELECT e.id,e.wo_number,e.tech_name,e.excluded
                FROM entries e JOIN v3_entry_meta m ON m.entry_id=e.id
                WHERE e.shift_id=? AND m.imported_v3=1""",(shift_id,)).fetchall()
            for old in previous_imports:
                if (old["wo_number"],old["tech_name"]) not in current_keys:
                    c.execute("UPDATE entries SET excluded=1 WHERE id=?",(old["id"],))
                    c.execute("UPDATE v3_entry_meta SET selection_hidden=1 WHERE entry_id=?",(old["id"],))
        # Provenance permits revoking stale V3-imported PMs without deleting V2 PMs.
        new_pm_numbers={r["wo_number"] for r in records if r["kind"]=="pm"}
        with db._conn() as c:
            tracked={r["wo_number"]:r["was_preexisting"] for r in c.execute(
                "SELECT wo_number,was_preexisting FROM v3_pm_tracking WHERE shift_id=?",(shift_id,))}
            for wo in tracked.keys()-new_pm_numbers:
                if not tracked[wo]:
                    c.execute("DELETE FROM pm_wos WHERE shift_id=? AND wo_number=?",(shift_id,wo))
                c.execute("DELETE FROM v3_pm_tracking WHERE shift_id=? AND wo_number=?",(shift_id,wo))
            for wo in new_pm_numbers-tracked.keys():
                exists=c.execute("SELECT 1 FROM pm_wos WHERE shift_id=? AND wo_number=?",(shift_id,wo)).fetchone()
                c.execute("INSERT INTO v3_pm_tracking (shift_id,wo_number,was_preexisting) VALUES (?,?,?)",
                          (shift_id,wo,int(bool(exists))))
        for r in records:
            if r["kind"]=="pm":
                db.upsert_pm_wo(shift_id,r["wo_number"],r["asset_id"],r["tech_name"])
                continue
            existing=db.get_cmms_entry(shift_id,r["wo_number"],r["tech_name"])
            incoming={
                "unit":r["asset_id"],"reported_issue":r["reported_issue"],
                "cause_text":r["cause"],"remarks_list":r["remarks_list"],"rfr_type":r["rfr"]
            }
            if existing:
                with db._conn() as c:
                    m=c.execute("SELECT * FROM v3_entry_meta WHERE entry_id=?",(existing["id"],)).fetchone()
                    saved={
                        "unit":existing.get("unit") or "",
                        "reported_issue":m["reported_issue"] if m else (existing.get("issue") or "").split(" — ",1)[-1],
                        "cause_text":m["cause_text"] if m else "",
                        "remarks_list":_remarks(existing),
                        "rfr_type":m["rfr_type"] if m and "rfr_type" in m.keys() else "",
                    }
                    # Compare the new report to the previously imported source,
                    # NOT MST-edited text. Re-uploading an identical export must
                    # not repeatedly flag a deliberate manual correction.
                    original_source = json.loads(m["source_snapshot"]) if m and m["source_snapshot"] else saved
                    if "rfr_type" not in original_source:
                        # Older V3 imports predate the display-category field.
                        # Fill it without creating a false re-upload conflict.
                        original_source["rfr_type"] = incoming["rfr_type"]
                        if m:
                            c.execute("UPDATE v3_entry_meta SET rfr_type=?,source_snapshot=? WHERE entry_id=?",
                                      (incoming["rfr_type"],json.dumps(original_source),existing["id"]))
                    if original_source==incoming:
                        unchanged+=1
                    else:
                        c.execute("INSERT OR IGNORE INTO v3_entry_meta(entry_id,shift_id,reported_issue,cause_text,original_remarks,imported_v3,source_snapshot) VALUES (?,?,?,?,?,1,?)",
                                  (existing["id"],shift_id,saved["reported_issue"],saved["cause_text"],json.dumps(saved["remarks_list"]),json.dumps(saved)))
                        c.execute("UPDATE v3_entry_meta SET needs_review=1,pending_json=? WHERE entry_id=?",
                                  (json.dumps(incoming),existing["id"]))
                        pending+=1
            else:
                eid=db.upsert_cmms_entry(shift_id,r["wo_number"],r["asset_id"],r["reported_issue"],json.dumps(r["remarks_list"]),r["tech_name"])
                with db._conn() as c:
                    c.execute("INSERT INTO v3_entry_meta(entry_id,shift_id,reported_issue,cause_text,original_remarks,imported_v3,source_snapshot,rfr_type) VALUES (?,?,?,?,?,1,?,?)",
                              (eid,shift_id,r["reported_issue"],r["cause"],json.dumps(r["remarks_list"]),json.dumps(incoming),r["rfr"]))
                added+=1
        with db._conn() as c:
            c.execute("INSERT INTO v3_shift_source(shift_id,filename,rows_json) VALUES (?,?,?) "
                      "ON CONFLICT(shift_id) DO UPDATE SET filename=excluded.filename,rows_json=excluded.rows_json",
                      (shift_id,batch["filename"],batch["rows_json"]))
            # Persist the UNION of this confirm's selection with anyone previously
            # selected who simply isn't mentioned in this particular file -- never
            # shrink the remembered crew just because one upload's export happened
            # to be narrower. Only an explicit uncheck of someone visible in this
            # file actually drops them (handled above via `removed`).
            persisted_selection=selected|(old_selected-allowed)
            c.execute("INSERT INTO v3_shift_selection(shift_id,technicians_json,filename,imported_at) VALUES (?,?,?,?) "
                      "ON CONFLICT(shift_id) DO UPDATE SET technicians_json=excluded.technicians_json, filename=excluded.filename, imported_at=excluded.imported_at",
                      (shift_id,json.dumps(sorted(persisted_selection)),batch["filename"],datetime.now().strftime("%Y-%m-%d %H:%M")))
            c.execute("DELETE FROM v3_import_batches WHERE token=?",(token,))
        notice=f"Imported {added} new technician/WO entries; {unchanged} unchanged; {pending} flagged for review. {stats.get('unique_pm_wos',0)} distinct closed PM WOs in this import."
        skipped_parts=[]
        needs_review_parts=[]
        if stats.get("not_closed"):
            skipped_parts.append(f"{stats['not_closed']} not yet closed")
        if stats.get("outside_shift"):
            skipped_parts.append(f"{stats['outside_shift']} outside this shift's time window")
        if stats.get("ambiguous_shift"):
            needs_review_parts.append(f"{stats['ambiguous_shift']} flagged for MST review (timestamps don't clearly establish the shift)")
        if split_pending:
            needs_review_parts.append(f"{len(split_pending)} need an explicit shift ownership call")
        if owned_elsewhere:
            # Common and EXPECTED when a Cetaris export spans multiple shifts
            # or days -- these WOs' work-start evidence confidently belongs to
            # a different, already-recorded shift (nothing to review -- it's
            # not ambiguous, just not this shift's). Surfaced so "0 new
            # entries" never reads as "something's broken".
            skipped_parts.append(f"{owned_elsewhere} already belong to a different shift's record")
        all_parts=skipped_parts+needs_review_parts
        if all_parts:
            tail=" -- see Needs Review for details)." if needs_review_parts else ")."
            notice += " Skipped " + " and ".join(all_parts) + " (not counted as happenings in this shift" + tail
        if breakdown_updates:
            notice += f" {breakdown_updates} breakdown WO(s) updated in the OOS/RTS tracker."
        # Import diagnostics (2026-10-24 facility request, triggered by a real
        # multi-shift overnight export): always shown, not just on failure, so
        # a low or zero count is never ambiguous between "nothing happened
        # this shift" and "the import is broken". source_rows/parsed_rows come
        # from parse_excel() at stage time; everything else from this confirm.
        review_count=len(split_pending)+stats.get("ambiguous_shift",0)
        excluded_bits=[]
        if owned_elsewhere: excluded_bits.append(f"{owned_elsewhere} belong to a different shift")
        if stats.get("not_closed"): excluded_bits.append(f"{stats['not_closed']} not yet closed")
        if stats.get("outside_shift"): excluded_bits.append(f"{stats['outside_shift']} outside shift window")
        if parse_stats.get("skipped_no_tech"): excluded_bits.append(f"{parse_stats['skipped_no_tech']} unassigned (no technician on record)")
        if parse_stats.get("skipped_no_wo"): excluded_bits.append(f"{parse_stats['skipped_no_wo']} missing WO number")
        notice += (
            f" | Diagnostics: {parse_stats.get('source_rows',len(all_rows))} source rows, "
            f"{parse_stats.get('parsed_rows',len(all_rows))} parsed, "
            f"{len({r['wo_number'] for r in all_rows})} unique WOs seen, "
            f"{len(rows)} attributed to this shift, "
            f"{stats.get('unique_pm_wos',0)} PMs, {stats.get('unique_happening_wos',0)} Shift Happenings, "
            f"{review_count} need MST review"
            + (", excluded: " + "; ".join(excluded_bits) if excluded_bits else "") + "."
        )
        from urllib.parse import quote
        return RedirectResponse(f"/v3?shift_id={shift_id}&notice={quote(notice)}",status_code=303)

    @router.get("/happening/{entry_id}",response_class=HTMLResponse)
    async def view_happening(request:Request,entry_id:int,shift_id:int):
        shift=secure(request,shift_id)
        rows=group_for_entry(shift_id,entry_id)
        with db._conn() as c:
            meta={r["entry_id"]:dict(r) for r in c.execute("SELECT * FROM v3_entry_meta WHERE shift_id=?",(shift_id,))}
        items=[]
        for r in rows:
            m=meta.get(r["id"],{})
            items.append({**r, "remarks": "\n".join(_remarks(r)),
                          "cause_text":m.get("cause_text") or "",
                          "reported_issue":m.get("reported_issue") or (r.get("issue") or "").split(" — ",1)[-1],
                          "needs_review":m.get("needs_review",0), "pending":json.loads(m["pending_json"]) if m.get("pending_json") else None})
        return templates.TemplateResponse(request,"v3/happening_modal.html",{"shift":shift,"items":items,"happening":items[0]})

    @router.post("/happening/{entry_id}/edit")
    async def edit_happening(request:Request,entry_id:int,shift_id:int=Form(...),reported_issue:str=Form(""),cause_text:str=Form("")):
        secure(request,shift_id)
        group=group_for_entry(shift_id,entry_id)
        form=await request.form()
        with db._conn() as c:
            for r in group:
                remarks=str(form.get(f"remarks_{r['id']}",""))[:12000].strip()
                issue=reported_issue[:4000].strip()
                cause=cause_text[:4000].strip()
                raw=json.dumps([part.strip() for part in remarks.splitlines() if part.strip()])
                prefix=f"WO #{r['wo_number']} — " if r.get("wo_number") else ""
                c.execute("UPDATE entries SET issue=?, raw_remarks=? WHERE id=?",(prefix+issue,raw,r["id"]))
                c.execute("INSERT OR IGNORE INTO v3_entry_meta(entry_id,shift_id,reported_issue,cause_text,original_remarks) VALUES (?,?,?,?,?)",
                          (r["id"],shift_id,issue,cause,json.dumps(_remarks(r))))
                c.execute("UPDATE v3_entry_meta SET reported_issue=?,cause_text=?,mst_edited=1 WHERE entry_id=?",(issue,cause,r["id"]))
        return RedirectResponse(f"/v3?shift_id={shift_id}&notice=Happening+updated",status_code=303)

    @router.post("/happening/{entry_id}/exclude")
    async def exclude_happening(request:Request,entry_id:int,shift_id:int=Form(...),excluded:int=Form(...)):
        secure(request,shift_id)
        group=group_for_entry(shift_id,entry_id)
        for r in group:
            db.set_entry_excluded(r["id"],bool(excluded))
            with db._conn() as c:
                c.execute("UPDATE v3_entry_meta SET selection_hidden=0 WHERE entry_id=?",(r["id"],))
        return RedirectResponse(f"/v3?shift_id={shift_id}",status_code=303)

    @router.post("/happening/{entry_id}/review")
    async def review_happening(request:Request,entry_id:int,shift_id:int=Form(...),decision:str=Form(...)):
        secure(request,shift_id)
        group=group_for_entry(shift_id,entry_id)
        with db._conn() as c:
            for r in group:
                m=c.execute("SELECT * FROM v3_entry_meta WHERE entry_id=?",(r["id"],)).fetchone()
                if not m or not m["pending_json"]:
                    continue
                if decision=="accept":
                    incoming=json.loads(m["pending_json"])
                    issue=incoming["reported_issue"]
                    c.execute("UPDATE entries SET unit=?,issue=?,raw_remarks=? WHERE id=?",
                              (incoming["unit"],f"WO #{r['wo_number']} — {issue}",json.dumps(incoming["remarks_list"]),r["id"]))
                    c.execute("UPDATE v3_entry_meta SET reported_issue=?,cause_text=?,rfr_type=?,mst_edited=0,original_remarks=?,source_snapshot=?,needs_review=0,pending_json=NULL WHERE entry_id=?",
                              (issue,incoming["cause_text"],incoming.get("rfr_type",""),json.dumps(incoming["remarks_list"]),json.dumps(incoming),r["id"]))
                elif decision=="reject":
                    c.execute("UPDATE v3_entry_meta SET needs_review=0,source_snapshot=pending_json,pending_json=NULL WHERE entry_id=?",(r["id"],))
                else:
                    raise HTTPException(400,"Invalid review decision")
        return RedirectResponse(f"/v3?shift_id={shift_id}&notice=Review+recorded",status_code=303)

    # -- PM's Needing Completion: same upload/parse pipeline as the V2 lead panel, --
    # -- re-skinned for the V3 accordion and gated by the V3 `secure()` session check. --
    PM_UPLOAD_DIR = Path("pm_uploads")
    PM_UPLOAD_DIR.mkdir(exist_ok=True)
    ALLOWED_PM_EXTS = {".pdf", ".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg"}

    def render_pm_section(shift_id: int) -> str:
        files = db.get_pm_files(shift_id)
        rows = db.get_pending_pms(shift_id)
        if not files:
            file_html = '<p>No files uploaded yet.</p>'
        else:
            file_html = '<div class="d2-pm-files">' + "".join(
                f'<div class="d2-pm-file-row"><a href="/v3/pm-file/{f["id"]}/download?shift_id={shift_id}">{f["filename"]}</a>'
                f'<form method="post" action="/v3/pm-file/{f["id"]}/delete">'
                f'<input type="hidden" name="shift_id" value="{shift_id}">'
                f'<button type="submit" class="d2-pm-remove" aria-label="Remove {f["filename"]}">&times;</button></form></div>'
                for f in files
            ) + '</div>'
        if not rows:
            table_html = ""
        else:
            body = "".join(
                f'<tr><td>{r["wo_number"]}</td><td>{r["asset_num"]}</td><td>{r["pm_due_date"]}</td>'
                f'<td>{r["promise_date"]}</td><td>{r["wo_date"]}</td></tr>'
                for r in rows
            )
            table_html = ('<table class="d2-pm-table"><thead><tr><th>WO #</th><th>Asset</th><th>PM Due</th>'
                          f'<th>Promise</th><th>WO Date</th></tr></thead><tbody>{body}</tbody></table>')
        return f'<div id="v3-pm-file-list">{file_html}{table_html}</div>'

    @router.get("/pm-files", response_class=HTMLResponse)
    async def v3_pm_files(request: Request, shift_id: int):
        secure(request, shift_id)
        return HTMLResponse(render_pm_section(shift_id))

    @router.post("/pm-upload", response_class=HTMLResponse)
    async def v3_pm_upload(request: Request, shift_id: int = Form(...), pm_file: UploadFile = File(...)):
        secure(request, shift_id)
        ext = Path(pm_file.filename or "").suffix.lower()
        if ext not in ALLOWED_PM_EXTS:
            return HTMLResponse('<p class="d2-pm-error">File type not allowed. Use PDF, Excel, CSV or image.</p>', status_code=400)
        stored = f"{shift_id}_{uuid.uuid4().hex[:8]}{ext}"
        data = await pm_file.read()
        (PM_UPLOAD_DIR / stored).write_bytes(data)
        db.save_pm_file(shift_id, pm_file.filename or stored, stored, "v3-shift-owner")
        if ext in (".xlsx", ".xls", ".csv"):
            try:
                from pm_pending_parser import parse_pm_pending
                parsed = parse_pm_pending(data, pm_file.filename or "")
                if parsed:
                    db.save_pending_pms(shift_id, [{
                        "wo_number": r.wo_number, "wo_date": r.wo_date,
                        "pm_due_date": r.pm_due_date, "promise_date": r.promise_date,
                        "asset_num": r.asset_num,
                    } for r in parsed])
            except Exception as exc:
                print(f"[v3 pm] parse failed: {exc}", flush=True)
        return HTMLResponse(render_pm_section(shift_id))

    @router.get("/pm-file/{file_id}/download")
    async def v3_pm_download(request: Request, file_id: int, shift_id: int):
        secure(request, shift_id)
        with db._conn() as c:
            row = c.execute("SELECT * FROM pm_files WHERE id=? AND shift_id=?", (file_id, shift_id)).fetchone()
        if not row:
            raise HTTPException(404, "File not found")
        path = PM_UPLOAD_DIR / row["stored_name"]
        if not path.exists():
            raise HTTPException(404, "File missing from disk")
        return FileResponse(path, filename=row["filename"], media_type="application/octet-stream")

    @router.post("/pm-file/{file_id}/delete", response_class=HTMLResponse)
    async def v3_pm_delete(request: Request, file_id: int, shift_id: int = Form(...)):
        secure(request, shift_id)
        stored = db.delete_pm_file(file_id)
        if stored:
            p = PM_UPLOAD_DIR / stored
            if p.exists():
                p.unlink()
            db.clear_pending_pms(shift_id)
        return HTMLResponse(render_pm_section(shift_id))

    @router.post("/manual")
    async def add_manual(request:Request,shift_id:int=Form(...),unit:str=Form(""),issue:str=Form(""),cause:str=Form(""),remarks:str=Form(""),wo_number:str=Form("")):
        secure(request,shift_id)
        if not unit.strip() or not issue.strip():
            raise HTTPException(400,"Asset ID and reported issue are required")
        eid=db.add_entry(shift_id,"Shift Owner (Manual)",unit.strip()[:100],issue.strip()[:4000],remarks.strip()[:12000],"", "monitor")
        with db._conn() as c:
            if wo_number.strip():
                c.execute("UPDATE entries SET wo_number=? WHERE id=?",(wo_number.strip()[:50],eid))
            c.execute("INSERT INTO v3_entry_meta(entry_id,shift_id,reported_issue,cause_text,original_remarks,mst_edited,imported_v3,rfr_type) VALUES (?,?,?,?,?,1,0,'Work Request')",
                      (eid,shift_id,issue.strip()[:4000],cause.strip()[:4000],json.dumps([remarks.strip()] if remarks.strip() else [])))
        return RedirectResponse(f"/v3?shift_id={shift_id}&notice=Manual+happening+added",status_code=303)

    @router.post("/loto")
    async def loto(request:Request,shift_id:int=Form(...),verified:str=Form(""),missing_keys:str=Form("")):
        secure(request,shift_id)
        missing=missing_keys[:400].strip()
        db.save_loto(shift_id,verified=="1" and not missing,missing)
        return RedirectResponse(f"/v3?shift_id={shift_id}&notice=LOTO+saved",status_code=303)

    @router.post("/notes")
    async def notes(request:Request,shift_id:int=Form(...),notes:str=Form("")):
        secure(request,shift_id)
        db.save_shift_notes(shift_id,notes[:20000])
        return RedirectResponse(f"/v3?shift_id={shift_id}&notice=Notes+saved",status_code=303)

    @router.get("/preview",response_class=HTMLResponse)
    async def preview(request:Request,shift_id:int):
        shift=secure(request,shift_id)
        data=model(shift_id)
        return templates.TemplateResponse(request,"v3/preview.html",{"shift":shift,**data,"loto":db.get_loto(shift_id),"shift_notes":db.get_shift_notes(shift_id),"oos_units":db.get_oos_units(),"rts_units":db.get_rts_events(shift_id),"vendors":v3_vendors(shift_id),"carry_overs":db.get_carry_over_entries(shift_id)})

    return router

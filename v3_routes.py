"""V3-only MST Command Center. Legacy report/export functions remain internal."""
from __future__ import annotations

import json
import uuid
import hmac
import hashlib
import base64
import os
import secrets
import time
from datetime import date, datetime, timedelta, timezone
from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse

import db
import v3_breakdown
import v3_split
from v3_cetaris import parse_excel, select_records, technician_counts, MAX_SIZE


def create_v3_router(templates, is_lead_authed, can_manage_shift, resolve_my_shift,
                     shift_window, lead_redirect, get_mst_roster=None, verify_mst_pin=None,
                     start_eligibility=None, allowed_shift_days=None, now_func=None,
                     get_v2_cookie_value=None, legacy_lead_page=None, generate_report=None):
    router = APIRouter(prefix="/v3", tags=["Shift V3"])

    # Same signed V3 session can be validated by the original V2 handlers.
    from v3_identity import COOKIE as v3_cookie, sign as signed_session, read as read_session, owns as shift_owned_by

    def decode_session(request):
        if get_mst_roster is None:
            return None  # compatible with isolated pre-auth V3 extraction tests
        return read_session(request, get_mst_roster())

    def accessible_shifts(request):
        who=decode_session(request)
        return [shift for shift in db.get_active_shifts() if shift_owned_by(shift,who)]

    def set_session_cookies(response, who, sid):
        response.set_cookie(v3_cookie, signed_session(who["associate"],sid,elevated=bool(who.get("elevated")) or bool(who["profile"].get("is_manager"))), max_age=12*3600,
                            httponly=True, samesite="strict", path="/", secure=False)
        # V3 is the only authenticated UI; clear any stale V2 cookies.
        for cookie in ("lead_auth","lead_shift_id","mgr_session",
                       "tech_assoc","tech_shift","tech_name"):
            response.delete_cookie(cookie,path="/")

    def _v3_live_shifts():
        now = now_func() if now_func else datetime.now()
        visible = []
        for shift in db.get_active_shifts():
            try:
                window = shift_window(shift["shift_type"], date.fromisoformat(shift["date"]))
                if not window:
                    continue
                if now > window[1]:
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
            if window and window[0] - timedelta(hours=1) <= now <= window[1]:
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
        if not profile.get("is_mst"):
            return entry_page(request,"Only authorized MST associates can start or resume shifts.")
        # Never accept the synthetic manager identifier as a login credential.
        if profile.get("is_manager"):
            return entry_page(request, "Use verified management access.")
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
        # codes to overlap, each under their own starter's identity.
        if any(sh["shift_type"]==shift_type for sh in _v3_live_shifts()):
            return entry_page(request,f"{shift_type} is already active. Resume your own shift or contact its starter.")
        if len(_v3_live_shifts())>=2:
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
        set_session_cookies(response,who,sid)
        return response

    @router.post("/master/verify", response_class=HTMLResponse)
    async def verify_master(request:Request, master_code:str=Form(""), associate_id:str=Form("")):
        # Both credentials must be explicitly configured on the workshop server.
        # Neither associate number alone nor a hardcoded code grants access.
        roster = get_mst_roster() if get_mst_roster else {}
        associate = associate_id.strip()
        shared = os.environ.get("IND3_V3_MASTER_CODE","")
        personal_assoc = os.environ.get("IND3_V3_PERSONAL_MASTER_ASSOC","").strip()
        personal_secret = os.environ.get("IND3_V3_PERSONAL_MASTER_SECRET","")
        if associate:
            valid = bool(personal_assoc and personal_secret and associate==personal_assoc
                         and hmac.compare_digest(master_code.strip(),personal_secret))
            profile = roster.get(associate) if valid else None
            if not profile or profile.get("is_manager"):
                return entry_page(request,"Management verification failed.")
            profile = {**profile,"is_manager":True}
        else:
            valid = bool(shared and hmac.compare_digest(master_code.strip(),shared))
            profile = roster.get("8972") if valid else None
            if not profile or not profile.get("is_manager"):
                return entry_page(request,"Management verification failed.")
            associate = "8972"
        response = entry_page(request, master_verified=True)
        set_session_cookies(response, {"associate":associate,"profile":profile,"elevated":True}, 0)
        return response

    @router.post("/master/select")
    async def master_select(request:Request, shift_type:str=Form(...)):
        who=decode_session(request)
        if not who or not who["profile"].get("is_manager"):
            return RedirectResponse("/v3/login", status_code=303)
        day=_v3_shift_day(shift_type) if shift_type in ("S1","S2","S3","S4","S5") else None
        if not day:
            return entry_page(request, "Shift is outside its working window.", master_verified=True)
        active=_v3_live_shifts()
        chosen=next((sh for sh in active if sh["shift_type"]==shift_type),None)
        if not chosen:
            if len(active)>=2:
                return entry_page(request, "Two shifts are already active.", master_verified=True)
            try:
                sid=db.start_shift(day.isoformat(),shift_type,
                                   who["profile"].get("display_name") or "Manager",
                                   opened_by_role="mst",is_ot=False,opened_by_assoc=who["associate"])
            except ValueError:
                return entry_page(request, "Unable to start shift.", master_verified=True)
        else:
            sid=chosen["id"]
        response=RedirectResponse(f"/v3?shift_id={sid}",status_code=303)
        set_session_cookies(response,who,sid)
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
            selection_cols={row[1] for row in c.execute("PRAGMA table_info(v3_shift_selection)")}
            if "split_techs_json" not in selection_cols:
                c.execute("ALTER TABLE v3_shift_selection ADD COLUMN split_techs_json TEXT NOT NULL DEFAULT '[]'")
            pm_cols={row[1] for row in c.execute("PRAGMA table_info(v3_pm_tracking)")}
            if "techs_json" not in pm_cols:
                c.execute("ALTER TABLE v3_pm_tracking ADD COLUMN techs_json TEXT NOT NULL DEFAULT '[]'")

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
        v3_breakdown.ensure_tables(db)
        return shift

    def model(shift_id):
        entries = db.get_entries(shift_id)
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
            r["cause_text"] = m.get("cause_text") or ""
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
            "happenings": list(groups.values()),
            "pm_wos": [dict(p) for p in pm_rows],
            "pm_count": len(pm_rows),
            "selected_techs": json.loads(selection["technicians_json"]) if selection else [],
            "selected_split_techs": json.loads(selection["split_techs_json"]) if selection else [],
            "imported_filename": selection["filename"] if selection else "",
            "imported_at": selection["imported_at"] if selection else "",
            "pending_breakdowns": v3_breakdown.pending(db),
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
        v3_breakdown.hold(db,unit,True)
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
        v3_breakdown.hold(db,unit,False)
        return return_workspace(shift_id,"Asset returned to service")

    @router.post("/rts/undo")
    async def v3_undo_rts(request:Request,shift_id:int=Form(...),unit:str=Form(...)):
        secure(request,shift_id)
        if unit not in db.get_rts_events(shift_id): raise HTTPException(404,"RTS record not found")
        db.delete_rts_event(shift_id,unit)
        return return_workspace(shift_id,"RTS log entry removed")

    @router.post("/breakdown/confirm")
    async def confirm_breakdown_rts(request:Request,shift_id:int=Form(...)):
        secure(request,shift_id)
        submitted=await request.form()
        allowed={r["wo_number"] for r in v3_breakdown.pending(db)}
        decisions={}
        for key,value in submitted.multi_items():
            if key.startswith("decision_"):
                wo=key[len("decision_"):]
                if wo in allowed and value in ("rts","keep_oos"):
                    decisions[wo]=value
        v3_breakdown.decide(db,shift_id,decisions)
        return return_workspace(shift_id,"Breakdown RTS decisions saved")

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

    def roster_technician_counts(counts, shift):
        """Annotate detected technicians from the existing Drax roster."""
        import re
        def key(name):
            parts=re.findall(r"[a-z]+",str(name).casefold())
            return tuple(sorted((parts[0],parts[-1]))) if len(parts)>=2 else tuple(parts)
        assigned=set()
        if get_mst_roster:
            for person in get_mst_roster().values():
                if str(person.get("shift","")).strip().upper()!=shift["shift_type"]:
                    continue
                name=person.get("full_name") or person.get("display_name") or ""
                if name: assigned.add(key(name))
        return [{**c,"assigned":key(c["name"]) in assigned} for c in counts]

    def render(request, shift, **extra):
        info = model(shift["id"])
        if extra.get("staged"):
            extra["staged"]={**extra["staged"],
                             "counts":roster_technician_counts(extra["staged"]["counts"],shift)}
        context = {
            "shift":shift,
            **info,
            "loto":db.get_loto(shift["id"]),
            "oos_units":db.get_oos_units(),
            "rts_units":db.get_rts_events(shift["id"]),
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

    @router.get("/analytics", response_class=HTMLResponse)
    async def v3_analytics(request:Request, shift_id:int=0):
        if get_mst_roster is not None:
            if not decode_session(request):
                return RedirectResponse("/v3/login",status_code=303)
        elif not is_lead_authed(request):
            return lead_redirect("/v3/analytics")
        return templates.TemplateResponse(request,"v3/analytics.html",{
            "shift_id":shift_id,
            "data":db.get_analytics(),
        })

    @router.get("/admin", response_class=HTMLResponse)
    async def v3_admin(request:Request):
        who=decode_session(request)
        if not who or not who["profile"].get("is_manager"):
            raise HTTPException(403,"Management access required")
        return templates.TemplateResponse(request,"v3/admin.html",{
            "dc_number":db.get_setting("dc_number","IND3"),
            "email_to":db.get_setting("email_to",""),
            "email_from_name":db.get_setting("email_from_name","IND3 Shift Passdown"),
            "ms_client_id":db.get_setting("ms_client_id",""),
            "roster_count":len(get_mst_roster() if get_mst_roster else {}),
            "notice":request.query_params.get("notice",""),
        })

    @router.post("/admin/settings")
    async def v3_admin_settings(request:Request,dc_number:str=Form(...),
                                email_to:str=Form(""),email_from_name:str=Form(""),
                                ms_client_id:str=Form("")):
        who=decode_session(request)
        if not who or not who["profile"].get("is_manager"):
            raise HTTPException(403,"Management access required")
        dc_number=dc_number.strip().upper()
        if not dc_number or len(dc_number)>20:
            raise HTTPException(400,"Invalid distribution center")
        db.set_setting("dc_number",dc_number)
        db.set_setting("email_to",email_to.strip()[:2000])
        db.set_setting("email_from_name",email_from_name.strip()[:160] or "IND3 Shift Passdown")
        db.set_setting("ms_client_id",ms_client_id.strip()[:300])
        return RedirectResponse("/v3/admin?notice=Configuration+saved",status_code=303)

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
        import inspect
        optional={"extra_title":[],"extra_note":[],"vendor_name":[],
                  "ro_item":[],"rts_unit":[]}
        supported=inspect.signature(generate_report).parameters
        await generate_report(request,shift_id=shift_id,
                              **{k:v for k,v in optional.items() if k in supported})
        report=db.get_last_report_for_shift(shift_id)
        if not report:
            raise HTTPException(500,"Report was not saved")
        return RedirectResponse(f"/v3/report/{report['id']}?shift_id={shift_id}",status_code=303)

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

    @router.post("/import",response_class=HTMLResponse)
    async def stage_import(request:Request,shift_id:int=Form(...),file:UploadFile=File(...)):
        shift=secure(request,shift_id)
        if not file.filename or not file.filename.lower().endswith(".xlsx"):
            return render(request,shift,warning="Please upload a Cetaris Desktop .xlsx report.")
        data=await file.read(MAX_SIZE + 1)
        try:
            records=parse_excel(data)
        except ValueError as exc:
            return render(request,shift,warning=str(exc))
        if not records:
            return render(request,shift,warning="No technician labor rows found in this report.")
        token=uuid.uuid4().hex
        with db._conn() as c:
            # Old unconfirmed imports are staging data, not shift history.
            c.execute("DELETE FROM v3_import_batches WHERE created_at < ?", ((datetime.now()-timedelta(days=1)).isoformat(timespec="seconds"),))
        with db._conn() as c:
            c.execute("INSERT INTO v3_import_batches (token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)",
                    (token,shift_id,file.filename[:240],datetime.now().isoformat(timespec="seconds"),json.dumps(records)))
        return render(request,shift,staged={
            "token":token,"filename":file.filename,"counts":technician_counts(records),
            "total_rows":len(records),
        })

    @router.post("/import/{token}/confirm")
    async def confirm_import(request:Request,token:str,shift_id:int=Form(...),techs:list[str]=Form(default=[]),split_techs:list[str]=Form(default=[])):
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
        allowed={r["tech_name"] for r in rows}
        selected=set(techs) & allowed
        if not selected:
            return render(request,shift,warning="Select at least one technician before extracting.",staged={
                "token":token,"filename":batch["filename"],"counts":technician_counts(rows),"total_rows":len(rows)})
        window=shift_window(shift["shift_type"],date.fromisoformat(shift["date"]))
        if not window:
            return render(request,shift,warning="Shift timing is not configured. Import cannot continue.")
        # Attribute all selected weekend technicians; S5 grace needs an
        # explicit confirmation of who worked S5, not a guessed roster.
        split_selected=set(split_techs) & selected if shift["shift_type"]=="S5" else set()
        submitted=await request.form()
        decisions={key[len("split_wo_"):]:value for key,value in submitted.multi_items()
                   if key.startswith("split_wo_") and value in ("S4","S5")}
        attributed,unresolved=v3_split.assign(
            rows, selected if shift["shift_type"] in ("S4","S5") else set(),
            shift["shift_type"], shift["date"], decisions,
            confirmed_s5_techs=split_selected)
        if unresolved:
            return render(request,shift,
                warning="Choose the owning shift for each ambiguous S4/S5 work order.",
                staged={"token":token,"filename":batch["filename"],
                        "counts":technician_counts(rows),"total_rows":len(rows),
                        "split_review":unresolved,
                        "checked_techs":sorted(selected),
                        "checked_split_techs":sorted(split_selected)})
        records,stats=select_records(attributed,selected,*window)
        v3_breakdown.sync(db,rows,selected,shift_id)
        added,unchanged,pending=0,0,0
        # Use short transactions. Existing db helpers open their own connections;
        # nesting them inside an active SQLite write transaction risks 'database locked'.
        with db._conn() as c:
            previous=c.execute("SELECT technicians_json FROM v3_shift_selection WHERE shift_id=?",(shift_id,)).fetchone()
        old_selected=set(json.loads(previous[0])) if previous else set()
        removed=old_selected-selected
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
        # Cumulative imports: missing WOs are retained. Only explicitly
        # present non-qualifying WOs may revoke earlier imported completion.
        incoming_by_wo = {}
        for raw in rows:
            if raw["tech_name"] in selected:
                incoming_by_wo.setdefault(raw["wo_number"], []).append(raw)
        eligible_happenings = {r["wo_number"] for r in records if r["kind"] == "happening"}
        eligible_pms = {r["wo_number"] for r in records if r["kind"] == "pm"}
        with db._conn() as c:
            previous_imports = c.execute("""SELECT e.id,e.wo_number,m.mst_edited
                FROM entries e JOIN v3_entry_meta m ON m.entry_id=e.id
                WHERE e.shift_id=? AND m.imported_v3=1""", (shift_id,)).fetchall()
            for old in previous_imports:
                wo = old["wo_number"]
                if wo in incoming_by_wo and wo not in eligible_happenings:
                    if old["mst_edited"]:
                        c.execute("UPDATE v3_entry_meta SET needs_review=1 WHERE entry_id=?", (old["id"],))
                    else:
                        c.execute("UPDATE entries SET excluded=1 WHERE id=?", (old["id"],))
                        c.execute("UPDATE v3_entry_meta SET selection_hidden=1 WHERE entry_id=?", (old["id"],))
        current_pm_techs={}
        for r in records:
            if r["kind"]=="pm":
                current_pm_techs.setdefault(r["wo_number"],set()).add(r["tech_name"])
        with db._conn() as c:
            tracked={r["wo_number"]:dict(r) for r in c.execute(
                "SELECT wo_number,was_preexisting,techs_json FROM v3_pm_tracking WHERE shift_id=?",(shift_id,))}
            for wo,meta in tracked.items():
                old_techs=set(json.loads(meta["techs_json"] or "[]"))
                # Backfill pre-migration attribution from last stored Cetaris export.
                if not old_techs:
                    prior=c.execute("SELECT rows_json FROM v3_shift_source WHERE shift_id=?",(shift_id,)).fetchone()
                    if prior:
                        old_techs={r["tech_name"] for r in json.loads(prior["rows_json"])
                                  if r["wo_number"]==wo and r["rfr"].strip().casefold() in
                                  ("preventive maintenance","preventative maintenance")}
                contributors=(old_techs & selected) | current_pm_techs.get(wo,set())
                revoked=(wo in incoming_by_wo and wo not in eligible_pms)
                deselected=(bool(old_techs) and not contributors)
                if revoked or deselected:
                    if not meta["was_preexisting"]:
                        c.execute("DELETE FROM pm_wos WHERE shift_id=? AND wo_number=?",(shift_id,wo))
                    c.execute("DELETE FROM v3_pm_tracking WHERE shift_id=? AND wo_number=?",(shift_id,wo))
                elif contributors:
                    c.execute("UPDATE v3_pm_tracking SET techs_json=? WHERE shift_id=? AND wo_number=?",
                              (json.dumps(sorted(old_techs|current_pm_techs.get(wo,set()))),shift_id,wo))
            for wo in eligible_pms-tracked.keys():
                exists=c.execute("SELECT 1 FROM pm_wos WHERE shift_id=? AND wo_number=?",(shift_id,wo)).fetchone()
                c.execute("INSERT OR IGNORE INTO v3_pm_tracking (shift_id,wo_number,was_preexisting,techs_json) VALUES (?,?,?,?)",
                          (shift_id,wo,int(bool(exists)),json.dumps(sorted(current_pm_techs.get(wo,set())))))
        for r in records:
            if r["kind"]=="oos":
                continue  # Breakdown OOS requires separate state tracking
            if r["kind"]=="pm":
                db.upsert_pm_wo(shift_id,r["wo_number"],r["asset_id"],r["tech_name"])
                continue
            existing=db.get_cmms_entry(shift_id,r["wo_number"],r["tech_name"])
            incoming={
                "unit":r["asset_id"],"reported_issue":r["reported_issue"],
                "cause_text":r["reported_issue"],"remarks_list":r["remarks_list"],"rfr_type":r["rfr"]
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
                              (eid,shift_id,r["reported_issue"],r["reported_issue"],json.dumps(r["remarks_list"]),json.dumps(incoming),r["rfr"]))
                added+=1
        with db._conn() as c:
            c.execute("INSERT INTO v3_shift_source(shift_id,filename,rows_json) VALUES (?,?,?) "
                      "ON CONFLICT(shift_id) DO UPDATE SET filename=excluded.filename,rows_json=excluded.rows_json",
                      (shift_id,batch["filename"],batch["rows_json"]))
            c.execute("INSERT INTO v3_shift_selection(shift_id,technicians_json,filename,imported_at,split_techs_json) VALUES (?,?,?,?,?) "
                      "ON CONFLICT(shift_id) DO UPDATE SET technicians_json=excluded.technicians_json,filename=excluded.filename,imported_at=excluded.imported_at,split_techs_json=excluded.split_techs_json",
                      (shift_id,json.dumps(sorted(selected)),batch["filename"],datetime.now().strftime("%Y-%m-%d %H:%M"),json.dumps(sorted(split_selected))))
            c.execute("DELETE FROM v3_import_batches WHERE token=?",(token,))
        notice=f"Imported {added} new technician/WO entries; {unchanged} unchanged; {pending} flagged for review. {stats.get('unique_pm_wos',0)} distinct closed PM WOs in this import."
        skipped_parts=[]
        if stats.get("not_closed"):
            skipped_parts.append(f"{stats['not_closed']} not yet closed")
        if stats.get("outside_shift"):
            skipped_parts.append(f"{stats['outside_shift']} outside this shift's time window")
        if skipped_parts:
            notice += " Skipped " + " and ".join(skipped_parts) + " (not counted as happenings)."
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

    @router.post("/manual")
    async def add_manual(request:Request,shift_id:int=Form(...),unit:str=Form(""),issue:str=Form(""),remarks:str=Form(""),wo_number:str=Form("")):
        secure(request,shift_id)
        if not unit.strip() or not issue.strip():
            raise HTTPException(400,"Asset ID and reported issue are required")
        eid=db.add_entry(shift_id,"Shift Owner (Manual)",unit.strip()[:100],issue.strip()[:4000],remarks.strip()[:12000],"", "monitor")
        with db._conn() as c:
            if wo_number.strip():
                c.execute("UPDATE entries SET wo_number=? WHERE id=?",(wo_number.strip()[:50],eid))
            c.execute("INSERT INTO v3_entry_meta(entry_id,shift_id,reported_issue,original_remarks,mst_edited,imported_v3,rfr_type) VALUES (?,?,?,?,1,0,'Work Request')",
                      (eid,shift_id,issue.strip()[:4000],json.dumps([remarks.strip()] if remarks.strip() else [])))
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

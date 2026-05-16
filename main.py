"""FastAPI app – Shift Passdown Generator (multi-tech edition)."""
import asyncio
import hashlib
import socket
from datetime import date, datetime, time, timedelta
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import json
from pathlib import Path

import httpx
import associates as assoc_mod
import db
import registry
from report_models import OosUnit, ReportData, RewrittenHappening
from formatter import format_html_preview, format_email
from cetaris_parser import parse_cetaris_csv, count_skipped
from summarizer import summarize_remarks
from exporters import export_docx, export_pdf

# Assets loaded from assets.json at startup (sourced from Asset_ID.xlsx)
_ASSETS_PATH = Path(__file__).resolve().parent / "assets.json"
_ASSETS: list[str] = json.loads(_ASSETS_PATH.read_text(encoding="utf-8"))
print(f"[startup] assets.json @ {_ASSETS_PATH} ({len(_ASSETS)} assets)", flush=True)

# Associate roster — keyed by associate number string
_ROSTER: dict[str, dict] = assoc_mod.load_associates()
print(f"[startup] roster loaded: {len(_ROSTER)} entries (incl. manager bypass)", flush=True)


# ── WebSocket connection manager ─────────────────────────────────────────────
class _WsManager:
    def __init__(self):
        self._clients: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self._clients.append(ws)

    def disconnect(self, ws: WebSocket):
        self._clients = [c for c in self._clients if c is not ws]

    async def broadcast(self, msg: dict):
        dead = []
        for client in self._clients:
            try:
                await client.send_json(msg)
            except Exception:
                dead.append(client)
        for d in dead:
            self._clients = [c for c in self._clients if c is not d]


_ws = _WsManager()

# ── Teams push notifications ─────────────────────────────────────────────────
_NOTIFY_PRIORITIES = {"down", "degraded"}

async def _notify_teams(priority: str, tech: str, unit: str, issue: str) -> None:
    """POST an Adaptive Card to the configured Teams incoming webhook (fire-and-forget)."""
    url = db.get_setting("teams_webhook_url", "")
    if not url or priority not in _NOTIFY_PRIORITIES:
        return
    is_down   = priority == "down"
    color     = "EA1100" if is_down else "FFC220"
    emoji     = "🔴" if is_down else "🟡"
    label     = "UNIT DOWN" if is_down else "Unit Degraded"
    payload   = {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": color,
        "summary": f"{emoji} {label} — {unit}",
        "sections": [{
            "activityTitle": f"{emoji} **{label} — {unit}**",
            "activitySubtitle": f"Logged by {tech} · IND3 Shift Passdown",
            "facts": [{"name": "Issue", "value": issue}],
            "markdown": True,
        }],
    }
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            await client.post(url, json=payload)
    except Exception as exc:
        print(f"[teams] webhook error: {exc}", flush=True)

# ── Tech auth helpers ────────────────────────────────────────────────────────
# Cookies: tech_assoc = associate number, tech_shift = shift ID at login
_TECH_ASSOC_COOKIE = "tech_assoc"
_TECH_SHIFT_COOKIE = "tech_shift"
# Legacy name cookie — clear on login so old sessions don't linger
_TECH_NAME_COOKIE  = "tech_name"


def _lookup_associate(code: str) -> dict | None:
    """Return associate entry for the given code, or None if not found."""
    return _ROSTER.get(code.strip())


def _get_tech_session(request: Request, shift_id: int) -> dict | None:
    """Return associate dict if session cookie is valid for this shift, else None."""
    code  = request.cookies.get(_TECH_ASSOC_COOKIE, "").strip()
    shift = request.cookies.get(_TECH_SHIFT_COOKIE, "")
    if code and shift == str(shift_id):
        return _ROSTER.get(code)
    return None


# ── Lead PIN auth ─────────────────────────────────────────────────────────────
_LEAD_PIN_DEFAULT = hashlib.sha256(b"8972").hexdigest()
_EMERGENCY_CODE   = "8972"   # always works even if PIN was changed — this IS the manager code
_LEAD_COOKIE      = "lead_auth"
_MGR_COOKIE       = "mgr_session"   # set ONLY when 8972 is entered
_MGR_TOKEN        = hashlib.sha256(b"mgr:8972").hexdigest()  # opaque, never sent to client


def _get_pin_hash() -> str:
    """Always read from DB so PIN changes via admin take effect immediately."""
    return db.get_setting("lead_pin_hash", _LEAD_PIN_DEFAULT)


def _check_lead_credential(entered: str) -> bool:
    """Accepts: stored PIN, hardcoded emergency code, OR any associate marked is_mst."""
    entered = entered.strip()
    if not entered:
        return False
    # 1. Matches the stored PIN hash (handles 8972 default + custom PINs)
    if hashlib.sha256(entered.encode()).hexdigest() == _get_pin_hash():
        return True
    # 2. Hardcoded emergency/manager override — always works
    if entered == _EMERGENCY_CODE:
        return True
    # 3. Valid associate marked as MST in the roster
    assoc = _ROSTER.get(entered)
    if assoc and assoc.get("is_mst"):
        return True
    return False


def _is_lead_authed(request: Request) -> bool:
    return request.cookies.get(_LEAD_COOKIE) == _get_pin_hash()


def _is_manager_authed(request: Request) -> bool:
    """True only when the session was opened with the hardcoded 8972 code."""
    return request.cookies.get(_MGR_COOKIE) == _MGR_TOKEN

def _lead_redirect(dest: str = "/lead"):
    return RedirectResponse(f"/lead/login?next={dest}", status_code=303)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _ROSTER
    # 1. Boot the facility registry
    registry.init_registry()
    active_slug = registry.get_active_slug()
    db.set_db_path(registry.db_path_for(active_slug))
    print(f"[startup] active facility: {active_slug}  db: {db.get_db_path()}", flush=True)

    # 2. Boot the facility DB
    db.init_db()
    db.purge_orphaned_shifts()
    db.purge_expired_ot_codes()
    db.init_settings(
        lead_pin_hash=_LEAD_PIN_DEFAULT,
        dc_number=active_slug,
    )

    # 3. Seed associates from Excel if DB table is empty
    db.seed_associates(_ROSTER)

    # 4. Reload roster from DB so admin edits survive restarts
    _ROSTER = db.associates_to_roster()
    print(f"[startup] roster (from DB): {len(_ROSTER)} entries", flush=True)

    # 5. Sync dc_number into template globals
    fac = registry.get_facility(active_slug)
    _sync_facility_globals(fac or {"slug": active_slug, "name": active_slug})

    # 6. Background Drax file watcher — auto-syncs roster when the Excel changes
    async def _drax_watcher():
        import os
        last_mtime: float = 0.0
        try:
            drax_path = assoc_mod.resolve_drax_path()
            last_mtime = os.path.getmtime(drax_path) if drax_path.exists() else 0.0
        except Exception:
            pass
        while True:
            await asyncio.sleep(5 * 60)  # poll every 5 minutes
            try:
                drax_path = assoc_mod.resolve_drax_path()  # re-resolve so path changes take effect
                if not drax_path.exists():
                    continue
                mtime = os.path.getmtime(drax_path)
                if mtime != last_mtime:
                    last_mtime = mtime
                    global _ROSTER
                    fresh = assoc_mod.load_associates()
                    db.force_reseed_associates(fresh)
                    _ROSTER = db.associates_to_roster()
                    print(f"[drax-watcher] file changed — reloaded {len(_ROSTER)-1} associates", flush=True)
            except Exception as exc:
                print(f"[drax-watcher] error: {exc}", flush=True)

    asyncio.create_task(_drax_watcher())

    # 7. Shift reaper — auto-terminates shifts that overstay their welcome
    async def _shift_reaper() -> None:
        """Check every 60 s; close any active shift that has passed its
        scheduled end time + 1-hour grace period."""
        # Check immediately on boot — catches shifts left open across restarts
        await asyncio.sleep(10)
        while True:
            try:
                for shift in db.get_active_shifts():
                    deadline = _shift_deadline(shift)
                    if deadline and datetime.now() >= deadline:
                        print(
                            f"[reaper] Auto-terminating {shift['shift_type']} "
                            f"(id={shift['id']}) — deadline was {deadline:%Y-%m-%d %H:%M}",
                            flush=True,
                        )
                        db.close_shift(shift["id"])
            except Exception as exc:
                print(f"[reaper] error: {exc}", flush=True)
            await asyncio.sleep(60)

    asyncio.create_task(_shift_reaper())
    yield


def _sync_facility_globals(fac: dict) -> None:
    """Push active-facility info into Jinja globals so all templates see it."""
    slug = fac.get("slug", fac.get("name", "?"))
    templates.env.globals["dc_number"]    = slug
    templates.env.globals["facility_name"] = fac.get("name", slug)
    templates.env.globals["facility_lat"]  = fac.get("lat") or ""
    templates.env.globals["facility_lng"]  = fac.get("lng") or ""


app = FastAPI(title="Shift Passdown Generator", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# ── Site-wide config exposed to all templates ─────────────────────────────────
DC_NUMBER = "US.08972"
templates.env.globals["dc_number"] = DC_NUMBER

# ── Shift schedule & auto-termination ──────────────────────────────────────────────
# Each entry: shift_type → (scheduled_end_time, end_is_next_calendar_day)
# Auto-terminate fires 1 hour after scheduled_end_time (grace period).
_SHIFT_SCHEDULE: dict[str, tuple[time, bool]] = {
    "S1": (time(16,  0), False),   # 5:30 AM → 4:00 PM,  terminate @ 5:00 PM
    "S2": (time(22, 30), False),   # 12:00 PM → 10:30 PM, terminate @ 11:30 PM
    "S3": (time( 7,  0), True),    # 8:30 PM  → 7:00 AM+1, terminate @ 8:00 AM+1
    "S4": (time(18,  0), False),   # 5:30 AM  → 6:00 PM,  terminate @ 7:00 PM
    "S5": (time( 6,  0), True),    # 5:30 PM  → 6:00 AM+1, terminate @ 7:00 AM+1
}
_SHIFT_GRACE = timedelta(hours=1)

# Allowed days-of-week per shift type (Monday=0 … Sunday=6)
_WEEKDAY_SHIFTS: dict[str, set[int]] = {
    "S1": {0, 1, 2, 3},   # Mon–Thu
    "S2": {0, 1, 2, 3},   # Mon–Thu
    "S3": {0, 1, 2, 3},   # Mon–Thu
    "S4": {4, 5, 6},      # Fri–Sun
    "S5": {4, 5, 6},      # Fri–Sun
}


def _shift_deadline(shift: dict) -> datetime | None:
    """Return the auto-terminate datetime for a shift, or None if type is unknown."""
    entry = _SHIFT_SCHEDULE.get(shift["shift_type"])
    if not entry:
        return None
    end_time, next_day = entry
    shift_date = date.fromisoformat(shift["date"])
    end_date   = shift_date + timedelta(days=1) if next_day else shift_date
    return datetime.combine(end_date, end_time) + _SHIFT_GRACE


def _fmt_time(val: str) -> str:
    """'2026-03-28 22:32:05'  →  '10:32 PM'"""
    try:
        h, m = int(val[11:13]), int(val[14:16])
        period = "AM" if h < 12 else "PM"
        return f"{h % 12 or 12}:{m:02d} {period}"
    except Exception:
        return val

templates.env.filters["fmt_time"] = _fmt_time

# Register urlencode filter — Jinja2 base env doesn't include it (that's a Flask-ism)
from urllib.parse import quote as _url_quote
templates.env.filters["urlencode"] = lambda s: _url_quote(str(s), safe="")


def _local_ip() -> str:
    """Best-effort local network IP for sharing with techs.

    Tries multiple methods so it works on Walmart Wi-Fi where
    external routes (8.8.8.8) may be blocked by proxy.
    """
    # Method 1: UDP routing trick — try several targets, stop at first hit
    for target in ("10.0.0.1", "172.16.0.1", "192.168.1.1", "8.8.8.8"):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0.2)
            s.connect((target, 80))
            ip = s.getsockname()[0]
            s.close()
            if not ip.startswith("127."):
                return ip
        except Exception:
            pass

    # Method 2: hostname resolution
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if not ip.startswith("127."):
            return ip
    except Exception:
        pass

    return "localhost"


# ─────────────────────────────────────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def home(request: Request, error: str = ""):
    shifts = db.get_active_shifts()          # 0-2 active shifts
    shift  = shifts[0] if shifts else None   # backwards-compat for single-shift logic
    today = date.today().isoformat()
    now   = datetime.now()
    elapsed_hours: float = 0.0
    if shift:
        try:
            started = datetime.strptime(shift["created_at"], "%Y-%m-%d %H:%M:%S")
            elapsed_hours = (now - started).total_seconds() / 3600
        except Exception:
            elapsed_hours = 0.0
    day_name = now.strftime("%A")
    weekday  = now.weekday()
    valid_now = [s for s, days in _WEEKDAY_SHIFTS.items() if weekday in days]
    return templates.TemplateResponse(
        request, "home.html",
        {"shift": shift, "shifts": shifts, "today": today,
         "elapsed_hours": elapsed_hours,
         "day_name": day_name, "valid_shifts_today": valid_now, "error": error}
    )


@app.get("/share", response_class=HTMLResponse)
async def share_page(request: Request):
    shift = db.get_active_shift()
    host = f"{_local_ip()}:8765"
    return templates.TemplateResponse(
        request, "share.html", {"shift": shift, "host": host}
    )


@app.post("/shift/request-ot")
async def request_ot_code(
    assoc_num:       str = Form(...),
    requested_shift: str = Form(...),
):
    """MST hit this when their assigned shift doesn’t match what they’re trying to start.
    Generates a manager-approval OT code visible only in admin (with 8972).
    Returns JSON — does NOT reveal the code to the requester.
    """
    import secrets
    from fastapi.responses import JSONResponse
    assoc = _ROSTER.get(assoc_num.strip())
    if not assoc or not assoc.get("is_mst"):
        return JSONResponse({"ok": False, "error": "Associate not found or not an MST."}, status_code=400)

    code           = secrets.token_hex(3).upper()  # e.g. "A1B2C3"
    assigned_shift = assoc.get("shift", "—") or "—"
    db.create_ot_code(
        code, assoc_num.strip(), assoc["display_name"],
        assigned_shift, requested_shift,
    )
    print(f"[OT] Code generated for {assoc['display_name']} "
          f"({assigned_shift}->{requested_shift}): {code}", flush=True)
    return JSONResponse({"ok": True, "message": "OT request submitted. See your manager to get the approval code."})


@app.post("/shift/check-ot")
async def check_ot_code(
    assoc_num:       str = Form(...),
    requested_shift: str = Form(...),
    ot_code:         str = Form(...),
):
    """Validate an OT code client-side before form submission."""
    from fastapi.responses import JSONResponse
    valid = db.validate_ot_code(ot_code.strip().upper(), assoc_num.strip(), requested_shift)
    if valid:
        return JSONResponse({"ok": True})
    return JSONResponse({"ok": False, "error": "Invalid, expired, or already-used code."}, status_code=400)


@app.post("/shift/start")
async def shift_start(
    request: Request,
    shift_date:  str = Form(...),
    shift_type:  str = Form(...),
    lead_name:   str = Form(...),
    assoc_num:   str = Form(""),
    ot_code:     str = Form(""),
):
    # Two-shift cap — reject if already 2 running simultaneously
    if len(db.get_active_shifts()) >= 2:
        return RedirectResponse(
            "/?error=Two+shifts+are+already+running.+End+one+before+starting+another.",
            status_code=303,
        )

    # Day-of-week validation
    try:
        parsed_date = date.fromisoformat(shift_date)
    except ValueError:
        return RedirectResponse("/?error=Invalid+date+format", status_code=303)

    allowed_days = _WEEKDAY_SHIFTS.get(shift_type)
    if allowed_days is not None and parsed_date.weekday() not in allowed_days:
        day_label = parsed_date.strftime("%A")
        rule = ("S1, S2 and S3 run Monday – Thursday only"
                if shift_type in ("S1", "S2", "S3")
                else "S4 and S5 run Friday – Sunday only")
        err = f"{shift_type}+cannot+start+on+a+{day_label}.+{rule}."
        return RedirectResponse(f"/?error={err}", status_code=303)

    # ── Shift-assignment validation — OT code check DISABLED (hidden for future use)
    # MSTs may start any shift without a code. Re-enable by restoring the block below.
    # FUTURE USE:
    #   assoc = _ROSTER.get(assoc_num)
    #   if assoc and assoc.get("is_mst"):
    #       assigned = (assoc.get("shift") or "").strip()
    #       if assigned and assigned != shift_type:
    #           code = ot_code.strip().upper()
    #           if not code or not db.validate_ot_code(code, assoc_num, shift_type):
    #               return RedirectResponse("/?error=Invalid+OT+code", status_code=303)
    #           db.consume_ot_code(code)

    # ── All good — stash in cookie and redirect to PIN ───────────────────────
    import json
    from urllib.parse import quote
    pending = json.dumps({"date": shift_date, "shift_type": shift_type, "lead_name": lead_name})
    resp = RedirectResponse(
        f"/lead/login?next={quote('/lead?status=started')}", status_code=303
    )
    resp.set_cookie("__pending_shift", pending,
                    httponly=True, samesite="lax", path="/", max_age=600)
    return resp


@app.post("/shift/close")
async def shift_close(request: Request, shift_id: int = Form(...)):
    if not _is_lead_authed(request):
        return _lead_redirect("/lead")
    db.close_shift(shift_id)
    return RedirectResponse("/", status_code=303)


@app.get("/lead/force-end")
async def lead_force_end(request: Request):
    """Force-close the active shift with no report — for when previous MST forgot to end."""
    if not _is_lead_authed(request):
        return _lead_redirect("/lead/force-end")
    shift = db.get_active_shift()
    if shift:
        db.close_shift(shift["id"])
    return RedirectResponse("/", status_code=303)


@app.post("/shift/end", response_class=HTMLResponse)
async def shift_end(request: Request, shift_id: int = Form(...)):
    if not _is_lead_authed(request):
        return _lead_redirect("/lead")
    shift = db.get_active_shift()
    if not shift or shift["id"] != shift_id:
        raise HTTPException(status_code=404, detail="Shift not found")

    # Prefer the last lead-generated report; fall back to auto-building one
    last = db.get_last_report_for_shift(shift_id)
    if last:
        content = last["content"]
    else:
        raw_entries = db.get_entries(shift_id)
        happenings_input = [
            HappeningInput(
                title=f"{e['unit']} – {e['issue'][:50]}",
                raw_note=f"{e['issue']}. {e['action']}. {e['result']}.",
            )
            for e in raw_entries
        ]
        rewritten = rewrite_all(happenings_input)
        data = ReportData(
            date=shift["date"],
            shift_type=shift["shift_type"],
            lead_name=shift["lead_name"],
            dc_number=DC_NUMBER,
            happenings=happenings,
        )
        content = format_email(data)
        db.save_report(shift_id, content, data.model_dump_json())

    db.close_shift(shift_id)

    carry_overs = db.get_carry_over_entries()
    shift_notes = db.get_shift_notes(shift_id)

    return templates.TemplateResponse(
        request, "shift_ended.html",
        {
            "shift": shift,
            "content": content,
            "carry_overs": carry_overs,
            "shift_notes": shift_notes,
        },
    )


@app.websocket("/ws/feed")
async def ws_feed(websocket: WebSocket):
    """MST dashboard subscribes here; gets pinged on every new tech entry."""
    await _ws.connect(websocket)
    try:
        while True:
            await websocket.receive_text()  # keep-alive; client sends pings
    except WebSocketDisconnect:
        _ws.disconnect(websocket)


# ─────────────────────────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────────────────────────

@app.get("/tech", response_class=HTMLResponse)
async def tech_page(request: Request):
    # Resolve shift from the tech's own cookie so both shifts work during overlaps
    raw_sid = request.cookies.get(_TECH_SHIFT_COOKIE, "")
    shift   = db.get_shift(int(raw_sid)) if raw_sid.isdigit() else None
    if not shift or not shift.get("is_active"):
        # Cookie stale or no cookie — fall back to newest active
        shifts = db.get_active_shifts()
        shift  = shifts[0] if shifts else None
    if not shift:
        return RedirectResponse("/", status_code=303)
    session = _get_tech_session(request, shift["id"])
    if not session:
        return RedirectResponse("/tech/login", status_code=303)
    entries   = db.get_entries(shift["id"])
    oos_units = db.get_oos_units()
    return templates.TemplateResponse(
        request, "tech.html",
        {
            "shift":      shift,
            "entries":    entries,
            "tech_name":  session["display_name"],
            "is_manager": session.get("is_manager", False),
            "assets":     _ASSETS,
            "oos_units":  oos_units,
        }
    )


@app.get("/api/associate-lookup")
async def associate_lookup(code: str = ""):
    """JS quick-lookup — returns name + is_mst flag for a given associate number."""
    from fastapi.responses import JSONResponse
    assoc = _ROSTER.get(code.strip())
    if not assoc:
        return JSONResponse({"found": False})
    return JSONResponse({
        "found":        True,
        "display_name": assoc["display_name"],
        "full_name":    assoc["full_name"],
        "shift":        assoc.get("shift", ""),
        "is_mst":       bool(assoc.get("is_mst", False)),
        "is_manager":   bool(assoc.get("is_manager", False)),
    })


@app.get("/tech/login", response_class=HTMLResponse)
async def tech_login_get(request: Request, next: str = "/tech", shift_id: int = 0):
    shifts = db.get_active_shifts()
    if not shifts:
        return RedirectResponse("/", status_code=303)
    active_ids = {s["id"] for s in shifts}
    chosen_id  = shift_id if shift_id in active_ids else shifts[0]["id"]
    matched    = [s for s in shifts if s["id"] == chosen_id]
    chosen     = matched[0] if matched else shifts[0]
    if _get_tech_session(request, chosen["id"]):
        return RedirectResponse("/tech", status_code=303)
    return templates.TemplateResponse(
        request, "tech_login.html",
        {"shift": chosen, "shifts": shifts, "error": "", "next": next}
    )


@app.post("/tech/login", response_class=HTMLResponse)
async def tech_login_post(
    request: Request,
    assoc_code: str = Form(...),
    shift_id:   int = Form(0),
    next: str = Form("/tech"),
):
    shifts = db.get_active_shifts()
    if not shifts:
        return RedirectResponse("/", status_code=303)
    active_ids = {s["id"] for s in shifts}
    chosen_id  = shift_id if shift_id in active_ids else shifts[0]["id"]
    matched    = [s for s in shifts if s["id"] == chosen_id]
    shift      = matched[0] if matched else shifts[0]

    entry = _lookup_associate(assoc_code)
    if entry is None:
        return templates.TemplateResponse(
            request, "tech_login.html",
            {"shift": shift, "shifts": shifts,
             "error": "Associate number not found. Try again or see your MST lead.",
             "next": next}
        )

    safe_next = next if next.startswith("/") else "/tech"
    resp = RedirectResponse(safe_next, status_code=303)
    resp.set_cookie(_TECH_ASSOC_COOKIE, entry["assoc_num"],  httponly=False, samesite="lax", path="/")
    resp.set_cookie(_TECH_SHIFT_COOKIE, str(shift["id"]),    httponly=False, samesite="lax", path="/")
    resp.delete_cookie(_TECH_NAME_COOKIE, path="/")
    return resp


@app.get("/tech/logout")
async def tech_logout():
    resp = RedirectResponse("/tech/login", status_code=303)
    resp.delete_cookie(_TECH_ASSOC_COOKIE, path="/")
    resp.delete_cookie(_TECH_SHIFT_COOKIE, path="/")
    resp.delete_cookie(_TECH_NAME_COOKIE,  path="/")
    return resp


@app.post("/tech/entry", response_class=HTMLResponse)
async def tech_add_entry(
    request: Request,
    shift_id: int = Form(...),
    tech_name: str = Form(...),
    unit: str = Form(...),
    summary: str = Form(...),
    priority: str = Form("monitor"),
):
    if not all([tech_name.strip(), unit.strip(), summary.strip()]):
        return HTMLResponse(
            '<p class="text-red-500 text-sm font-semibold p-2">All fields are required.</p>',
            status_code=400,
        )
    safe_priority = priority if priority in ("down", "degraded", "monitor") else "monitor"
    db.add_entry(shift_id, tech_name.strip(), unit.strip(),
                 summary.strip(), "", "", priority=safe_priority)
    entries = db.get_entries(shift_id)
    # Signal connected MST dashboards via WebSocket
    entry_count = len(entries)
    await _ws.broadcast({"type": "new_entry", "tech": tech_name.strip(),
                         "unit": unit.strip(), "count": entry_count,
                         "priority": safe_priority})
    # Teams push notification (fire-and-forget, won't delay tech response)
    asyncio.create_task(_notify_teams(safe_priority, tech_name.strip(),
                                      unit.strip(), summary.strip()))
    return templates.TemplateResponse(
        request, "partials/my_entries.html",
        {"entries": entries, "tech_name": tech_name.strip()},
    )


@app.delete("/tech/entry/{entry_id}", response_class=HTMLResponse)
async def tech_delete_entry(
    request: Request,
    entry_id: int,
    shift_id: int,
    tech_name: str,
):
    db.delete_entry(entry_id)
    entries = db.get_entries(shift_id)
    return templates.TemplateResponse(
        request, "partials/my_entries.html",
        {"entries": entries, "tech_name": tech_name},
    )


# ── CMMS Import (Cetaris CSV upload) ─────────────────────────────────────────

async def _process_cmms_upload(
    request: Request,
    shift_id: int,
    file: UploadFile,
    uploader_name: str = "CMMS Import",
    include_entries_oob: bool = False,
) -> HTMLResponse:
    """Shared logic for both tech and lead CMMS CSV uploads."""
    import json as _json

    shift = db.get_shift(shift_id)
    if not shift:
        return HTMLResponse(
            '<p class="text-red-500 text-sm font-semibold p-3">❌ Shift not found.</p>',
            status_code=404,
        )

    if not file.filename or not file.filename.lower().endswith((".csv", ".xlsx", ".xls")):
        return HTMLResponse(
            '<p class="text-red-500 text-sm font-semibold p-3">❌ Please upload a CSV or Excel file exported from Cetaris.</p>',
            status_code=400,
        )

    content = await file.read()
    work_orders = parse_cetaris_csv(content)
    skipped     = count_skipped(content)

    if not work_orders:
        return templates.TemplateResponse(
            request, "partials/cmms_import_result.html",
            {"imported": [], "skipped": skipped, "error": "No maintenance work orders found in this file.",
             "include_entries_oob": False},
        )

    imported = []
    for wo in work_orders:
        existing_remarks = db.get_cmms_raw_remarks(shift_id, wo.wo_number)
        merged_remarks   = list(dict.fromkeys(existing_remarks + wo.remarks))
        summary = await summarize_remarks(merged_remarks)
        db.upsert_cmms_entry(
            shift_id=shift_id,
            wo_number=wo.wo_number,
            asset_id=wo.asset_id,
            summary=summary,
            raw_remarks_json=_json.dumps(merged_remarks),
            tech_name=uploader_name,
        )
        imported.append({"wo": wo.wo_number, "asset": wo.asset_id, "summary": summary})

    await _ws.broadcast({"type": "cmms_import", "count": len(imported)})

    # For tech uploads: also send an OOB swap to refresh #my-entries
    entries = db.get_entries(shift_id) if include_entries_oob else []

    return templates.TemplateResponse(
        request, "partials/cmms_import_result.html",
        {
            "imported": imported,
            "skipped": skipped,
            "error": "",
            "include_entries_oob": include_entries_oob,
            "entries": entries,
            "tech_name": uploader_name,
        },
    )


@app.post("/tech/cmms-upload", response_class=HTMLResponse)
async def tech_cmms_upload(
    request: Request,
    shift_id: int  = Form(...),
    file: UploadFile = File(...),
):
    """Tech dashboard CMMS CSV upload endpoint."""
    assoc_code = request.cookies.get(_TECH_ASSOC_COOKIE, "")
    assoc = _ROSTER.get(assoc_code) if assoc_code else None
    if not assoc:
        return HTMLResponse(
            '<p class="text-red-500 text-sm font-semibold p-3">❌ Session expired. Please sign in again.</p>',
            status_code=401,
        )
    uploader_name = assoc.get("display_name") or assoc.get("full_name", "CMMS Import")
    return await _process_cmms_upload(
        request, shift_id, file,
        uploader_name=uploader_name,
        include_entries_oob=True,
    )


@app.post("/lead/cmms-upload", response_class=HTMLResponse)
async def lead_cmms_upload(
    request: Request,
    shift_id: int  = Form(...),
    file: UploadFile = File(...),
):
    """Lead/MST dashboard CMMS CSV upload endpoint."""
    if not _is_lead_authed(request):
        return _lead_redirect("/lead")
    shift = db.get_shift(shift_id)
    uploader_name = f"{shift['lead_name']} (MST)" if shift else "MST Import"
    return await _process_cmms_upload(
        request, shift_id, file,
        uploader_name=uploader_name,
        include_entries_oob=False,
    )


@app.get("/tech/entry/{entry_id}/edit-form", response_class=HTMLResponse)
async def tech_edit_form(
    request: Request,
    entry_id: int,
    shift_id: int,
    tech_name: str,
):
    """Return the inline edit form <li> swapped in for the read-only card."""
    entries = db.get_entries(shift_id)
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if not entry:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "partials/entry_edit_form.html",
        {"entry": entry, "tech_name": tech_name, "assets": _ASSETS},
    )


@app.post("/tech/entry/{entry_id}/edit", response_class=HTMLResponse)
async def tech_update_entry(
    request: Request,
    entry_id: int,
    shift_id: int = Form(...),
    tech_name: str = Form(...),
    unit: str = Form(...),
    summary: str = Form(...),
    priority: str = Form("monitor"),
):
    if not all([unit.strip(), summary.strip()]):
        return HTMLResponse(
            '<p class="text-red-500 text-sm font-semibold p-2">Unit and summary are required.</p>',
            status_code=400,
        )
    safe_priority = priority if priority in ("down", "degraded", "monitor") else "monitor"
    db.update_entry(entry_id, unit.strip(), summary.strip(), safe_priority)
    entries = db.get_entries(shift_id)
    return templates.TemplateResponse(
        request, "partials/my_entries.html",
        {"entries": entries, "tech_name": tech_name},
    )


@app.get("/tech/my-entries", response_class=HTMLResponse)
async def tech_my_entries(request: Request, shift_id: int, tech_name: str):
    """Used by the cancel button to restore the read-only list."""
    entries = db.get_entries(shift_id)
    return templates.TemplateResponse(
        request, "partials/my_entries.html",
        {"entries": entries, "tech_name": tech_name},
    )


# ─────────────────────────────────────────────────────────────────────────────
# MST  (PIN protected)
# ─────────────────────────────────────────────────────────────────

@app.post("/lead/auth", response_class=HTMLResponse)
async def lead_auth_json(request: Request, pin: str = Form(...)):
    """Lightweight credential check used by the home modal — returns JSON.
    Accepts: stored PIN, emergency code 8972, or any associate's ID marked is_mst.
    """
    from fastapi.responses import JSONResponse
    if _check_lead_credential(pin):
        resp = JSONResponse({"ok": True})
        resp.set_cookie(_LEAD_COOKIE, _get_pin_hash(),
                        httponly=True, samesite="lax", path="/")
        # Manager cookie — set ONLY when the hardcoded 8972 code is entered
        if pin.strip() == _EMERGENCY_CODE:
            resp.set_cookie(_MGR_COOKIE, _MGR_TOKEN,
                            httponly=True, samesite="lax", path="/")
        else:
            resp.delete_cookie(_MGR_COOKIE, path="/")
        return resp
    return JSONResponse({"ok": False, "error": "Incorrect credential. Try again."}, status_code=401)


@app.get("/lead/logout")
async def lead_logout():
    resp = RedirectResponse("/lead/login", status_code=303)
    resp.delete_cookie(_LEAD_COOKIE, path="/")
    resp.delete_cookie(_MGR_COOKIE,  path="/")
    return resp


@app.get("/lead/login", response_class=HTMLResponse)
async def lead_login_get(request: Request, next: str = "/lead"):
    # Always show the PIN page — no bypass even if cookie exists.
    # The cookie only keeps HTMX/API calls alive within an open session.
    return templates.TemplateResponse(
        request, "lead_login.html", {"next": next, "error": ""}
    )


@app.post("/lead/login", response_class=HTMLResponse)
async def lead_login_post(
    request: Request,
    pin: str = Form(...),
    next: str = Form("/lead"),
):
    import json
    entered = pin.strip()
    if _check_lead_credential(entered):
        # Credential OK — create any pending shift
        pending_raw = request.cookies.get("__pending_shift")
        if pending_raw:
            try:
                p = json.loads(pending_raw)
                db.start_shift(p["date"], p["shift_type"], p["lead_name"])
            except Exception as exc:
                print(f"[login] pending shift cookie invalid: {exc}", flush=True)

        safe_next = next if next.startswith("/") else "/lead"
        html = f"""
        <!DOCTYPE html><html><head>
          <meta http-equiv="refresh" content="0;url={safe_next}" />
        </head><body>
          <script>window.location.replace("{safe_next}");</script>
        </body></html>
        """
        resp = HTMLResponse(content=html)
        resp.set_cookie(_LEAD_COOKIE, _get_pin_hash(),
                        httponly=True, samesite="lax", path="/")
        resp.delete_cookie("__pending_shift", path="/")
        # Manager cookie — only granted when 8972 is the credential
        if entered == _EMERGENCY_CODE:
            resp.set_cookie(_MGR_COOKIE, _MGR_TOKEN,
                            httponly=True, samesite="lax", path="/")
        else:
            resp.delete_cookie(_MGR_COOKIE, path="/")
        return resp
    return templates.TemplateResponse(
        request, "lead_login.html",
        {"next": next, "error": "Incorrect associate ID or PIN. Please try again."},
    )


@app.get("/lead", response_class=HTMLResponse)
async def lead_page(request: Request, status: str = "", shift_id: int = 0):
    if not _is_lead_authed(request):
        return _lead_redirect("/lead")
    shifts = db.get_active_shifts()
    if not shifts:
        return RedirectResponse("/", status_code=303)
    # Honour shift_id param when there are multiple active (overlap window)
    active_ids = {s["id"] for s in shifts}
    matched    = [s for s in shifts if s["id"] == shift_id]
    shift      = matched[0] if matched else shifts[0]
    entries = db.get_entries(shift["id"])
    carry_overs = db.get_carry_over_entries()
    shift_notes = db.get_shift_notes(shift["id"])
    response = templates.TemplateResponse(
        request, "lead.html",
        {"shift": shift, "shifts": shifts, "entries": entries, "status": status,
         "assets": _ASSETS, "carry_overs": carry_overs, "shift_notes": shift_notes}
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/lead/tech-participation", response_class=HTMLResponse)
async def lead_tech_participation(request: Request):
    if not _is_lead_authed(request):
        return HTMLResponse('')
    shift = db.get_active_shift()
    if not shift:
        return HTMLResponse('')
    techs      = db.get_tech_participation(shift["id"])
    done_techs = db.get_done_techs(shift["id"])
    return templates.TemplateResponse(
        request, "partials/tech_participation.html",
        {"techs": techs, "done_techs": done_techs},
    )


@app.get("/asset/{unit}/history", response_class=HTMLResponse)
async def asset_history_page(request: Request, unit: str):
    if not _is_lead_authed(request):
        return _lead_redirect(f"/asset/{unit}/history")
    history = db.get_asset_history(unit)
    return templates.TemplateResponse(
        request, "asset_history.html", {"unit": unit, "history": history}
    )


@app.get("/history", response_class=HTMLResponse)
async def shift_history(request: Request):
    if not _is_lead_authed(request):
        return _lead_redirect("/history")
    shifts = db.list_past_shifts_with_counts(50)
    return templates.TemplateResponse(
        request, "history.html", {"shifts": shifts}
    )


@app.get("/history/shift/{shift_id}", response_class=HTMLResponse)
async def shift_report_view(request: Request, shift_id: int):
    if not _is_lead_authed(request):
        return _lead_redirect(f"/history/shift/{shift_id}")
    report = db.get_last_report_for_shift(shift_id)
    shifts = db.list_past_shifts(50)
    shift = next((s for s in shifts if s["id"] == shift_id), None)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    entries = db.get_entries(shift_id)
    stats   = db.get_asset_stats(shift_id)
    return templates.TemplateResponse(
        request, "shift_report_view.html",
        {"shift": shift, "report": report, "entries": entries, "stats": stats}
    )


@app.get("/history/shift/{shift_id}/assets", response_class=HTMLResponse)
async def shift_asset_view(request: Request, shift_id: int):
    if not _is_lead_authed(request):
        return _lead_redirect(f"/history/shift/{shift_id}/assets")
    stats = db.get_asset_stats(shift_id)
    shifts = db.list_past_shifts(50)
    shift = next((s for s in shifts if s["id"] == shift_id), None)
    if not shift:
        raise HTTPException(status_code=404, detail="Shift not found")
    return templates.TemplateResponse(
        request, "shift_report_view.html",
        {"shift": shift, "report": None, "stats": stats}
    )


@app.get("/history/weekly", response_class=HTMLResponse)
async def weekly_summary(request: Request, days: int = 28):
    if not _is_lead_authed(request):
        return _lead_redirect("/history/weekly")
    summary = db.get_weekly_summary(days)
    return templates.TemplateResponse(
        request, "weekly.html", {"summary": summary, "days": days}
    )


@app.get("/lead/asset-stats", response_class=HTMLResponse)
async def lead_asset_stats(request: Request):
    """HTMX polling endpoint — asset frequency badges + chart data."""
    if not _is_lead_authed(request):
        return HTMLResponse('')
    shift = db.get_active_shift()
    if not shift:
        return HTMLResponse('<p class="text-gray-400 italic text-sm p-2">No active shift.</p>')
    stats = db.get_asset_stats(shift["id"])
    recurring = db.get_recurring_assets(days=7, min_reports=3)
    return templates.TemplateResponse(
        request, "partials/asset_stats.html", {"stats": stats, "recurring": recurring}
    )


@app.get("/lead/feed", response_class=HTMLResponse)
async def lead_feed(request: Request):
    """HTMX polling endpoint – returns live entry feed partial.

    If the active shift has changed since the page loaded (different shift ID),
    we return HX-Refresh so the entire dashboard reloads clean with the new shift.
    """
    if not _is_lead_authed(request):
        return HTMLResponse('')   # silently empty — HTMX poll, not a page

    shift = db.get_active_shift()
    if not shift:
        return HTMLResponse(
            '<p class="text-gray-400 italic text-sm">No active shift.</p>',
            headers={"HX-Refresh": "true"}
        )

    # Detect shift change — force full page reload so header + form update too
    page_shift_id = request.headers.get("X-Shift-Id", "")
    if page_shift_id and str(shift["id"]) != page_shift_id:
        return HTMLResponse("", headers={"HX-Refresh": "true"})

    entries = db.get_entries(shift["id"])
    return templates.TemplateResponse(
        request, "partials/live_feed.html", {"entries": entries}
    )


# ── Carry-over toggle ────────────────────────────────────────────────────────────────

@app.post("/lead/entry/{entry_id}/carry-over", response_class=HTMLResponse)
async def toggle_carry_over(
    request: Request,
    entry_id: int,
    state: int = Form(1),  # 1 = flag, 0 = unflag
):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    db.set_carry_over(entry_id, bool(state))
    return HTMLResponse("")


# ── Shift notes ───────────────────────────────────────────────────────────────────────

@app.post("/lead/shift-notes", response_class=HTMLResponse)
async def save_shift_notes(
    request: Request,
    shift_id: int = Form(...),
    notes: str = Form(""),
):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    db.save_shift_notes(shift_id, notes)
    return HTMLResponse(
        '<span class="text-green-600 text-xs font-semibold">&#10003; Saved</span>'
    )


# ── Roster hot-reload ─────────────────────────────────────────────────────────────

@app.post("/admin/reload-roster", response_class=HTMLResponse)
async def reload_roster(request: Request):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    global _ROSTER
    try:
        drax_path = assoc_mod.resolve_drax_path()
        if not drax_path.exists():
            return HTMLResponse(
                '<span class="text-red-600 text-xs font-semibold">&#10007; Drax file not found — '
                'make sure OneDrive is synced and the file exists.</span>'
            )
        fresh = assoc_mod.load_associates()   # from Excel
        db.force_reseed_associates(fresh)     # upsert DB (preserves is_mst flags)
        _ROSTER = db.associates_to_roster()   # reload in-memory roster
        count = len(_ROSTER) - 1             # subtract manager entry
        return HTMLResponse(
            f'<span class="text-green-600 text-xs font-semibold">&#10003; Synced {count} associates from Drax — DB updated</span>'
        )
    except Exception as exc:
        return HTMLResponse(
            f'<span class="text-red-600 text-xs font-semibold">&#10007; Sync failed: {exc}</span>',
            status_code=500,
        )



@app.get("/admin", response_class=HTMLResponse)
async def admin_get(request: Request):
    if not _is_lead_authed(request):
        return _lead_redirect("/admin")
    is_mgr  = _is_manager_authed(request)
    ot_codes = db.get_pending_ot_codes() if is_mgr else []
    return templates.TemplateResponse(request, "admin.html", {
        "dc_number_val":     db.get_setting("dc_number", DC_NUMBER),
        "roster_count":      len(_ROSTER),
        "teams_webhook_url": db.get_setting("teams_webhook_url", ""),
        "msg":        request.query_params.get("msg", ""),
        "err":        request.query_params.get("err", ""),
        "is_manager": is_mgr,
        "ot_codes":   ot_codes,
    })


@app.post("/admin/update", response_class=HTMLResponse)
async def admin_update(
    request:     Request,
    action:      str = Form(...),
    dc_number:   str = Form(""),
    current_pin: str = Form(""),
    new_pin:     str = Form(""),
    confirm_pin: str = Form(""),
):
    if not _is_lead_authed(request):
        return _lead_redirect("/admin")
    from urllib.parse import quote

    if action == "dc":
        val = dc_number.strip().upper()
        if not val:
            return RedirectResponse("/admin?err=DC+number+cannot+be+empty", status_code=303)
        db.set_setting("dc_number", val)
        templates.env.globals["dc_number"] = val
        return RedirectResponse(f"/admin?msg=DC+number+updated+to+{quote(val)}", status_code=303)

    if action == "pin":
        if not all([current_pin.strip(), new_pin.strip(), confirm_pin.strip()]):
            return RedirectResponse("/admin?err=All+PIN+fields+required", status_code=303)
        if hashlib.sha256(current_pin.strip().encode()).hexdigest() != _get_pin_hash():
            return RedirectResponse("/admin?err=Current+PIN+is+incorrect", status_code=303)
        if new_pin.strip() != confirm_pin.strip():
            return RedirectResponse("/admin?err=New+PIN+and+confirm+do+not+match", status_code=303)
        if len(new_pin.strip()) < 4:
            return RedirectResponse("/admin?err=PIN+must+be+at+least+4+digits", status_code=303)
        new_hash = hashlib.sha256(new_pin.strip().encode()).hexdigest()
        db.set_setting("lead_pin_hash", new_hash)
        # Force re-login — clear the auth kie since hash changed
        resp = RedirectResponse("/admin?msg=PIN+updated+—+please+log+in+again", status_code=303)
        resp.delete_cookie(_LEAD_COOKIE, path="/")
        return resp

    return RedirectResponse("/admin", status_code=303)


@app.post("/admin/teams-webhook", response_class=HTMLResponse)
async def admin_save_teams_webhook(request: Request, webhook_url: str = Form("")):
    if not _is_lead_authed(request):
        return _lead_redirect("/admin")
    url = webhook_url.strip()
    db.set_setting("teams_webhook_url", url)
    msg = "Webhook saved — Teams notifications active ✅" if url else "Webhook cleared — Teams notifications off"
    return RedirectResponse(f"/admin?msg={msg.replace(' ', '+')}", status_code=303)


# ── Tech done toggle ──────────────────────────────────────────────────────────

@app.post("/tech/done", response_class=HTMLResponse)
async def tech_toggle_done(request: Request, shift_id: int = Form(...), tech_name: str = Form(...)):
    """Toggle tech's done state. Returns updated button HTML."""
    shift = db.get_active_shift()
    if not shift:
        return HTMLResponse("", status_code=400)
    is_done = db.toggle_tech_done(shift_id, tech_name)
    await _ws.broadcast({"type": "tech_done", "tech": tech_name, "done": is_done})
    if is_done:
        return HTMLResponse("""
            <button form="done-form" type="submit"
              class="w-full flex items-center justify-center gap-2 bg-green-600 hover:bg-green-700
                     text-white font-black py-3 rounded-xl transition text-sm">
              ✅ Signed off for this shift
            </button>""")
    return HTMLResponse("""
        <button form="done-form" type="submit"
          class="w-full flex items-center justify-center gap-2 bg-gray-100 hover:bg-gray-200
                 text-gray-700 border-2 border-gray-300 font-bold py-3 rounded-xl transition text-sm">
          &#10003; Done for the shift
        </button>""")


# ── Lead summary strip ────────────────────────────────────────────────────────

@app.get("/lead/summary", response_class=HTMLResponse)
async def lead_summary(request: Request):
    if not _is_lead_authed(request):
        return HTMLResponse("")
    shift = db.get_active_shift()
    if not shift:
        return HTMLResponse("")
    entries = db.get_entries(shift["id"])
    done    = db.get_done_techs(shift["id"])
    techs   = {e["tech_name"] for e in entries}
    downs   = sum(1 for e in entries if e["priority"] == "down")
    degs    = sum(1 for e in entries if e["priority"] == "degraded")
    return templates.TemplateResponse(request, "partials/summary_strip.html", {
        "total":    len(entries),
        "downs":    downs,
        "degs":     degs,
        "techs":    len(techs),
        "done":     len(done),
        "created":  shift["created_at"],
    })


# ── Duplicate unit check ────────────────────────────────────────────────────────

@app.get("/tech/check-unit")
async def tech_check_unit(shift_id: int, tech_name: str, unit: str):
    """Return JSON {duplicate: bool, time: str|null} for the tech's duplicate guard."""
    entries = db.get_entries(shift_id)
    match   = next(
        (e for e in entries
         if e["tech_name"] == tech_name
         and e["unit"].strip().upper() == unit.strip().upper()),
        None,
    )
    if match:
        return {"duplicate": True,  "time": match["created_at"]}
    return     {"duplicate": False, "time": None}



# ── Associate CRUD ──────────────────────────────────────────────────────────

@app.get("/admin/associates", response_class=HTMLResponse)
async def admin_assoc_list(request: Request):
    if not _is_lead_authed(request):
        return HTMLResponse("", status_code=403)
    associates = db.get_associates()
    return templates.TemplateResponse(
        request, "partials/assoc_list.html", {"associates": associates}
    )


@app.post("/admin/associates/add", response_class=HTMLResponse)
async def admin_assoc_add(
    request:    Request,
    assoc_num:  str = Form(...),
    full_name:  str = Form(...),
    shift:      str = Form(""),
    default_id: str = Form(""),
    is_mst:     str = Form("0"),
):
    global _ROSTER
    if not _is_lead_authed(request):
        return HTMLResponse("", status_code=403)
    if not assoc_num.strip() or not full_name.strip():
        return HTMLResponse(
            '<p class="text-red-600 text-xs font-semibold p-2">⚠️ Associate # and Name are required.</p>'
        )
    db.add_associate(assoc_num.strip(), full_name.strip(),
                     shift.strip(), default_id.strip(),
                     is_mst=(is_mst == "1"))
    _ROSTER = db.associates_to_roster()
    associates = db.get_associates()
    return templates.TemplateResponse(
        request, "partials/assoc_list.html", {"associates": associates}
    )


@app.post("/admin/associates/{assoc_num}/toggle-mst", response_class=HTMLResponse)
async def admin_assoc_toggle_mst(request: Request, assoc_num: str):
    global _ROSTER
    if not _is_lead_authed(request):
        return HTMLResponse("", status_code=403)
    assoc = db.get_associate(assoc_num)
    if not assoc:
        raise HTTPException(status_code=404)
    new_val = not bool(assoc.get("is_mst", 0))
    db.set_associate_mst(assoc_num, new_val)
    _ROSTER = db.associates_to_roster()
    associates = db.get_associates()
    return templates.TemplateResponse(
        request, "partials/assoc_list.html", {"associates": associates}
    )


@app.delete("/admin/associates/{assoc_num}", response_class=HTMLResponse)
async def admin_assoc_delete(request: Request, assoc_num: str):
    global _ROSTER
    if not _is_lead_authed(request):
        return HTMLResponse("", status_code=403)

    # 1. Remove from DB
    db.delete_associate(assoc_num)
    _ROSTER = db.associates_to_roster()

    # 2. Mirror deletion to the Drax Excel file (non-blocking, best-effort)
    drax_ok = await asyncio.to_thread(assoc_mod.remove_from_drax, assoc_num)

    associates = db.get_associates()
    resp = templates.TemplateResponse(
        request, "partials/assoc_list.html", {"associates": associates}
    )
    if not drax_ok:
        # Inject a one-shot warning below the table via OOB swap
        resp = templates.TemplateResponse(
            request, "partials/assoc_list.html",
            {
                "associates": associates,
                "drax_warn": "Removed from DB, but Drax file could not be updated — "
                             "it may be open in Excel or OneDrive is syncing. "
                             "Re-sync will restore the row unless you remove it manually.",
            }
        )
    return resp



# ── Print report ────────────────────────────────────────────────────────────

@app.get("/lead/print-report", response_class=HTMLResponse)
async def print_report(request: Request):
    if not _is_lead_authed(request):
        return _lead_redirect("/lead/print-report")
    shift = db.get_active_shift()
    if not shift:
        return RedirectResponse("/", status_code=303)
    entries = db.get_entries(shift["id"])
    carry_overs = db.get_carry_over_entries()
    shift_notes = db.get_shift_notes(shift["id"])
    return templates.TemplateResponse(
        request, "print_report.html",
        {"shift": shift, "entries": entries, "carry_overs": carry_overs,
         "shift_notes": shift_notes}
    )


# ── Print QR sheet ───────────────────────────────────────────────────────────────────

@app.get("/print-qr", response_class=HTMLResponse)
async def print_qr(request: Request):
    shift = db.get_active_shift()
    host = f"{_local_ip()}:8765"
    return templates.TemplateResponse(
        request, "print_qr.html", {"shift": shift, "host": host}
    )

@app.get("/oos/list", response_class=HTMLResponse)
async def oos_list(request: Request):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403, detail="Not authorised")
    units = db.get_oos_units()
    return templates.TemplateResponse(
        request, "partials/oos_list.html", {"units": units}
    )


@app.post("/oos/add", response_class=HTMLResponse)
async def oos_add(request: Request, unit: str = Form(...)):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403, detail="Not authorised")
    clean = unit.strip()
    if clean:
        db.add_oos_unit(clean)
    units = db.get_oos_units()
    return templates.TemplateResponse(
        request, "partials/oos_list.html", {"units": units}
    )


@app.get("/lead/rts-log", response_class=HTMLResponse)
async def lead_rts_log(request: Request):
    """Return the RTS log partial for the current shift (used on page load + OOB refresh)."""
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    shift = db.get_active_shift()
    rts_units = db.get_rts_events(shift["id"]) if shift else []
    html = templates.env.get_template("partials/rts_log.html").render(rts_units=rts_units)
    return HTMLResponse(html, headers={"HX-Trigger": json.dumps({"rtsUpdated": {"count": len(rts_units)}})})


@app.delete("/lead/rts-log/{unit}", response_class=HTMLResponse)
async def delete_rts_entry(request: Request, unit: str):
    """Remove a single entry from the current shift's RTS log."""
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    shift = db.get_active_shift()
    if shift:
        db.delete_rts_event(shift["id"], unit)
    rts_units = db.get_rts_events(shift["id"]) if shift else []
    html = templates.env.get_template("partials/rts_log.html").render(rts_units=rts_units)
    return HTMLResponse(html, headers={"HX-Trigger": json.dumps({"rtsUpdated": {"count": len(rts_units)}})})


@app.post("/oos/remove", response_class=HTMLResponse)
async def oos_remove(request: Request, unit: str = Form(...)):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403, detail="Not authorised")
    clean = unit.strip()
    # Log the RTS event against the active shift BEFORE removing from OOS
    shift = db.get_active_shift()
    db.log_rts_event(shift["id"] if shift else None, clean)
    db.remove_oos_unit(clean)

    # Render both partials: OOS list (primary target) + RTS log (OOB)
    units     = db.get_oos_units()
    rts_units = db.get_rts_events(shift["id"]) if shift else []
    oos_html  = templates.env.get_template("partials/oos_list.html").render(units=units)
    rts_html  = templates.env.get_template("partials/rts_log.html").render(rts_units=rts_units)
    combined  = (
        oos_html
        + f'\n<div id="rts-log-content" hx-swap-oob="innerHTML">{rts_html}</div>'
    )
    return HTMLResponse(
        combined,
        headers={"HX-Trigger": json.dumps({"rtsUpdated": {"count": len(rts_units)}})},
    )


@app.post("/lead/generate", response_class=HTMLResponse)
async def lead_generate(
    request: Request,
    shift_id: int = Form(...),
    extra_title: list[str] = Form(default=[]),
    extra_note:  list[str] = Form(default=[]),
    vendor_name: list[str] = Form(default=[]),
    ro_item:     list[str] = Form(default=[]),
    rts_unit:    list[str] = Form(default=[]),
):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403, detail="Not authorised")
    shift = db.get_active_shift()
    if not shift or shift["id"] != shift_id:
        raise HTTPException(status_code=404, detail="Shift not found")

    # RTS: merge DB log (from OOS panel removals) + any extra manual form entries
    rts_from_db   = db.get_rts_events(shift_id)
    rts_from_form = [u.strip() for u in rts_unit if u.strip()]
    # Preserve order: DB-logged first, then manual additions; no dupes
    seen: set[str] = set()
    rts_clean: list[str] = []
    for u in rts_from_db + rts_from_form:
        if u and u not in seen:
            seen.add(u)
            rts_clean.append(u)
    # Remove any RTS'd units that are still in OOS (edge case: form-only entries)
    db.remove_rts_from_oos(rts_from_form)

    # ── Pull FRESH entries straight from DB — no rewriting, no templates.
    # Show exactly what the technician typed: asset ID + their summary + their name.
    db_entries = db.get_entries(shift_id)
    happenings: list[RewrittenHappening] = [
        RewrittenHappening(
            title=e["unit"],
            paragraph=e["issue"].strip(),
            tech_name=e.get("tech_name", ""),
        )
        for e in db_entries
        if e["unit"].strip() or e["issue"].strip()
    ]

    # Append any MST-authored extra happenings from the form
    for t, n in zip(extra_title, extra_note):
        if t.strip() and n.strip():
            happenings.append(RewrittenHappening(title=t.strip(), paragraph=n.strip()))

    # OOS always comes from the persistent DB — not the form
    oos_units = db.get_oos_units()

    data = ReportData(
        date=shift["date"],
        shift_type=shift["shift_type"],
        lead_name=shift["lead_name"],
        dc_number=DC_NUMBER,
        vendors=[v.strip() for v in vendor_name if v.strip()],
        happenings=happenings,
        ro=[r.strip() for r in ro_item if r.strip()],
        rts=rts_clean,
        oos=[OosUnit(unit=u) for u in oos_units],
    )

    email_text   = format_email(data)
    preview_html = format_html_preview(data)
    report_json  = data.model_dump_json()
    report_id    = db.save_report(shift_id, email_text, report_json)

    return templates.TemplateResponse(
        request, "partials/report_output.html",
        {"preview_html": preview_html,
         "email_text":   email_text,
         "report_id":    report_id},
    )


@app.get("/report/{report_id}/docx")
async def download_docx(report_id: int):
    rec = db.get_report(report_id)
    if not rec or not rec.get("report_json"):
        raise HTTPException(status_code=404, detail="Report data not found")
    data  = ReportData.model_validate_json(rec["report_json"])
    fname = (
        f"passdown_{rec['generated_at'].replace(' ', '_').replace(':', '-')}.docx"
    )
    return Response(
        content=export_docx(data),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/report/{report_id}/pdf")
async def download_pdf(report_id: int):
    rec = db.get_report(report_id)
    if not rec or not rec.get("report_json"):
        raise HTTPException(status_code=404, detail="Report data not found")
    data  = ReportData.model_validate_json(rec["report_json"])
    fname = (
        f"passdown_{rec['generated_at'].replace(' ', '_').replace(':', '-')}.pdf"
    )
    return Response(
        content=export_pdf(data),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/report/{report_id}/download", response_class=PlainTextResponse)
async def download_report(report_id: int):
    rec = db.get_report(report_id)
    if not rec:
        raise HTTPException(status_code=404)
    fname = f"passdown_{rec['generated_at'].replace(' ', '_').replace(':', '-')}.txt"
    return PlainTextResponse(
        content=rec["content"],
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )

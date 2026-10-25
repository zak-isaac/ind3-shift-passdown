"""FastAPI app – Shift Passdown Generator (multi-tech edition)."""
import asyncio
import hashlib
import os
import socket
import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Form, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import json
from pathlib import Path

import httpx
from starlette.middleware.base import BaseHTTPMiddleware
import associates as assoc_mod
import db
import emailer
import signatures as _sigs
import outlook_sender
import pm_pending_parser
import registry

# ── Email via local Outlook COM (no passwords, no SMTP auth needed) ────────────
_OUTLOOK_OK: bool | None = None   # None = not checked yet

def _outlook_ready() -> bool:
    global _OUTLOOK_OK
    if _OUTLOOK_OK is None:
        _OUTLOOK_OK = outlook_sender.is_outlook_available()
    return _OUTLOOK_OK
from report_models import OosUnit, ReportData, RewrittenHappening, HappeningInput
from formatter import format_html_preview, format_email, format_email_passdown, format_email_passdown_html
from rewriter import rewrite_all
import ai_rewriter
from cetaris_parser import parse_cetaris, ParseResult
import cause_lookup
from summarizer import summarize_remarks
import spelling_fix
from exporters import export_docx, export_pdf


# ── Load .env file (no deps — built-in only) ──────────────────────────────────────────────
_DOTENV = Path(__file__).resolve().parent / ".env"
if _DOTENV.exists():
    for _line in _DOTENV.read_text(encoding="utf-8").splitlines():
        _line = _line.strip()
        if _line and not _line.startswith("#") and "=" in _line:
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())
    print(f"[startup] .env loaded from {_DOTENV}", flush=True)

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
        # Dev/test-mode traffic must NEVER surface toasts/notifications on a
        # real MST's live dashboard -- the sandbox DB already isolates the
        # data, this stops the side-channel too.
        if db.is_sandboxed():
            return
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
    if db.is_sandboxed():
        return  # never spam the real Teams channel from a test-mode session
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


# ── Email helper ──────────────────────────────────────────────────────────────────
async def _send_passdown_email(subject: str, body: str) -> None:
    """Fire-and-forget: send via local Outlook COM automation."""
    if db.is_sandboxed():
        print("[email] dev/test mode -- skipping real send", flush=True)
        return
    to_raw    = db.get_setting("email_to", "").strip()
    to_list   = [a.strip() for a in to_raw.split(",") if a.strip()]
    from_name = db.get_setting("email_from_name", "IND3 Shift Passdown").strip()
    reply_to  = db.get_setting("email_reply_to", "").strip() or (to_list[0] if to_list else "")
    if not to_list:
        print("[email] no recipients — skipping", flush=True)
        return
    try:
        await outlook_sender.send_email(
            to_list, subject, body,
            from_name=from_name,
            reply_to=reply_to,
        )
        print(f"[email] sent via Outlook → {to_list}", flush=True)
    except Exception as exc:
        print(f"[email] Outlook send failed: {exc}", flush=True)


# ── Tech auth helpers ───────────────────────────────────────────────────────────────
_TECH_SHIFT_COOKIE = "tech_shift"
_TECH_ASSOC_COOKIE = "tech_assoc"
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
# Master credentials — full manager/emergency override, same power as 8972.
# Add more associate numbers here later for other operators; every check in
# this file goes through _is_master_credential() so one list covers all of it.
_MASTER_CODES     = {_EMERGENCY_CODE, "2759"}   # 2759 = Isaac, same rights as 8972
_LEAD_COOKIE      = "lead_auth"
_LEAD_SHIFT_COOKIE = "lead_shift_id"   # which shift this MST session owns
_MGR_COOKIE       = "mgr_session"   # set when a master credential is entered
_MGR_TOKEN        = hashlib.sha256(b"mgr:8972").hexdigest()  # opaque, never sent to client


def _get_pin_hash() -> str:
    """Always read from DB so PIN changes via admin take effect immediately."""
    return db.get_setting("lead_pin_hash", _LEAD_PIN_DEFAULT)


def _is_master_credential(entered: str) -> bool:
    """True for any credential in _MASTER_CODES -- same override rights as 8972."""
    return entered.strip() in _MASTER_CODES


def _check_lead_credential(entered: str) -> bool:
    """Accepts: stored PIN, any master credential, OR any associate marked is_mst."""
    entered = entered.strip()
    if not entered:
        return False
    if hashlib.sha256(entered.encode()).hexdigest() == _get_pin_hash():
        return True
    if _is_master_credential(entered):
        return True
    assoc = _ROSTER.get(entered)
    if assoc and assoc.get("is_mst"):
        return True
    return False


_END_SHIFT_GRACE_HOURS = 24


def _find_end_shift_grace_shift(entered: str):
    """If `entered` matches the associate who personally OPENED an active
    tech-led shift (opened_by_role == 'tech', opened_by_assoc == entered),
    and it's been less than _END_SHIFT_GRACE_HOURS since that shift started,
    return the shift dict so they can be let into the MST dashboard to do
    the end-of-shift passdown. Otherwise return None.

    Scoped to the SPECIFIC shift they opened -- not a blanket MST grant, and
    not available to other techs on the same shift.
    """
    entered = entered.strip()
    if not entered:
        return None
    now = datetime.now()
    for s in db.get_active_shifts():
        if s.get("opened_by_role") != "tech":
            continue
        if (s.get("opened_by_assoc") or "").strip() != entered:
            continue
        try:
            started = datetime.strptime(s["created_at"], "%Y-%m-%d %H:%M:%S")
        except (ValueError, KeyError, TypeError):
            continue
        if (now - started) <= timedelta(hours=_END_SHIFT_GRACE_HOURS):
            return s
    return None


def _v3_authenticated(request: Request):
    """Only a valid, signed V3 session authorizes operational work."""
    from v3_identity import read
    return read(request, _ROSTER)


def _is_lead_authed(request: Request) -> bool:
    return _v3_authenticated(request) is not None


def _get_lead_shift_id(request: Request) -> int:
    session = _v3_authenticated(request)
    return int(session["shift_id"]) if session else 0


def _can_manage_shift(request: Request, shift_id: int) -> bool:
    """Do not accept V2 cookies, PINs, or guessed shift IDs."""
    from v3_identity import owns
    session = _v3_authenticated(request)
    shift = db.get_shift(shift_id)
    return bool(
        session and session["shift_id"] == shift_id
        and owns(shift, session)
    )


def _require_shift_access(request: Request, shift_id: int) -> None:
    """Raise 403 unless this session may manage `shift_id`. Call this at the
    top of every route that mutates a specific shift's data."""
    if not _can_manage_shift(request, shift_id):
        raise HTTPException(
            status_code=403,
            detail="This shift belongs to a different MST session. Open your "
                   "own shift from Home, or use the manager code to override.",
        )


def _resolve_my_shift(request: Request) -> dict | None:
    """The shift this MST session should act on by default: their own bound
    shift if they have one, otherwise -- only when there's truly no
    ambiguity (a master session, or exactly one shift running at all) --
    the most recently active shift. Never silently guesses among multiple
    concurrently active shifts for a regular, unbound MST session.
    """
    bound = _get_lead_shift_id(request)
    if bound:
        return db.get_shift(bound)
    shifts = db.get_active_shifts()
    if len(shifts) == 1:
        return shifts[0]
    if _is_manager_authed(request) and shifts:
        return shifts[0]
    return None


def _is_manager_authed(request: Request) -> bool:
    session = _v3_authenticated(request)
    return bool(session and session["profile"].get("is_manager"))


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
                    if deadline and _indianapolis_now() >= deadline:
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

    # 8. Legacy AI writer is dormant in V3. Do not probe or warm its obsolete
    # phi4-mini dependency; V3 uses qwen2.5:3b via v3_ollama_background.
    # The V3 worker connects to local Ollama only after a confirmed import.

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
# Screenshot walkthrough -- public, self-contained HTML+images. Replaces the
# old separate /guide and /tech-docs pages as the single onboarding doc.
app.mount("/walkthrough", StaticFiles(directory="docs/walkthrough", html=True), name="walkthrough")
# Raw technical docs (ARCHITECTURE.md, DEPLOYMENT.md) linked from the walkthrough footer.
app.mount("/docs", StaticFiles(directory="docs"), name="docs")
templates = Jinja2Templates(directory="templates")

# ── Site-wide config exposed to all templates ─────────────────────────────────
DC_NUMBER = "US.08972"
templates.env.globals["dc_number"] = DC_NUMBER

def _indianapolis_now() -> datetime:
    """Authoritative facility wall-clock for shift eligibility and expiration.

    Return naive Indianapolis time because shift windows are stored as naive
    facility-local datetimes; do not compare them to workstation-local clocks.
    """
    return datetime.now(ZoneInfo("America/Indiana/Indianapolis")).replace(tzinfo=None)

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


_SHIFT_START_TIME = {
    "S1": time(5, 30),
    "S2": time(12, 0),
    "S3": time(20, 30),
    "S4": time(5, 30),
    "S5": time(17, 30),
}


_SHIFT_EARLY_UNLOCK = timedelta(hours=1)  # window/button unlocks 1hr early, for handover


def _shift_window(shift_type, ref_date):
    start_time = _SHIFT_START_TIME.get(shift_type)
    entry = _SHIFT_SCHEDULE.get(shift_type)
    if not start_time or not entry:
        return None
    end_time, next_day = entry
    start_dt = datetime.combine(ref_date, start_time)
    end_date = ref_date + timedelta(days=1) if next_day else ref_date
    end_dt = datetime.combine(end_date, end_time) + _SHIFT_GRACE
    return start_dt, end_dt


def _is_within_shift_window(shift_type, now):
    for ref_date in (now.date(), now.date() - timedelta(days=1)):
        window = _shift_window(shift_type, ref_date)
        if window and (window[0] - _SHIFT_EARLY_UNLOCK) <= now <= window[1]:
            return True
    return False


def _check_shift_start_eligibility(assoc, shift_type, now):
    if not assoc:
        if not _is_within_shift_window(shift_type, now):
            return False, False, shift_type + " is not in its scheduled window right now."
        return True, False, ""
    if assoc.get("is_manager"):
        return True, False, ""
    assigned = (assoc.get("shift") or "").strip()
    is_mst = bool(assoc.get("is_mst"))
    name = assoc.get("display_name") or "You"
    if not _is_within_shift_window(shift_type, now):
        return False, False, shift_type + " is not in its scheduled window right now."
    if is_mst:
        if assigned and assigned != shift_type:
            return True, True, ""
        return True, False, ""
    if not assigned or assigned != shift_type:
        return False, False, name + " is assigned to " + (assigned or "a different shift") + ", not " + shift_type + "."
    return True, False, ""
    return False





def _fmt_time(val: str) -> str:
    """'2026-03-28 22:32:05'  →  '10:32 PM'"""
    try:
        h, m = int(val[11:13]), int(val[14:16])
        period = "AM" if h < 12 else "PM"
        return f"{h % 12 or 12}:{m:02d} {period}"
    except Exception:
        return val

_WO_PREFIX_RE = __import__('re').compile(
    r'^WO\s*#?\s*\d+\s*[\u2013\u2014\-]\s*', __import__('re').IGNORECASE
)

def _strip_wo_prefix(text: str) -> str:
    """Strip the 'WO #12345 - ' prefix Cetaris embeds in Tech Remarks."""
    return _WO_PREFIX_RE.sub('', text).strip()

templates.env.filters["fmt_time"]        = _fmt_time
templates.env.filters["strip_wo_prefix"] = _strip_wo_prefix

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


# ─────────────────────────────────────────────────────────────────────────────────────
# HOME
# ─────────────────────────────────────────────────────────────

@app.get("/v2-home", response_class=HTMLResponse)
async def home(request: Request, error: str = ""):
    shifts = db.get_active_shifts()          # 0-2 active shifts
    shift  = shifts[0] if shifts else None   # backwards-compat for single-shift logic
    now   = datetime.now()
    today = now.date().isoformat()
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


@app.get("/", include_in_schema=False)
async def root_redirect_to_v3():
    """Root now enters the app through V3. V2's legacy home page is still
    reachable at /v2-home for reference during the V2->V3 migration."""
    return RedirectResponse("/v3", status_code=303)


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
    confirm_dup: str = Form("0"),
):
    active_shifts = db.get_active_shifts()

    # Two-shift cap -- reject if already 2 running simultaneously
    if len(active_shifts) >= 2:
        return RedirectResponse(
            "/?error=Two+shifts+are+already+running.+End+one+before+starting+another.",
            status_code=303,
        )

    # Day-of-week validation
    try:
        parsed_date = date.fromisoformat(shift_date)
    except ValueError:
        return RedirectResponse("/?error=Invalid+date+format", status_code=303)

    # No future dates -- today or earlier only (today = frozen dev date in test mode)
    if parsed_date > date.today():
        return RedirectResponse(
            "/?error=Future+dates+are+not+allowed.+Pick+today+or+an+earlier+date.",
            status_code=303,
        )

    # Dev/test mode intentionally bypasses the weekday<->shift_type pairing
    # (frozen date is always a Friday) so every shift type can be exercised
    # on the single frozen test date. Production traffic keeps the real rule.
    allowed_days = _WEEKDAY_SHIFTS.get(shift_type)
    if allowed_days is not None and parsed_date.weekday() not in allowed_days:
        day_label = parsed_date.strftime("%A")
        rule = ("S1, S2 and S3 run Monday - Thursday only"
                if shift_type in ("S1", "S2", "S3")
                else "S4 and S5 run Friday - Sunday only")
        err = f"{shift_type}+cannot+start+on+a+{day_label}.+{rule}."
        return RedirectResponse(f"/?error={err}", status_code=303)

    entered = assoc_num.strip()
    assoc   = _ROSTER.get(entered) if entered else None
    is_mst  = bool(assoc and (assoc.get("is_mst") or assoc.get("is_manager")))

    # -- Duplicate-start guard -- a NON-MST tech trying to open a NEW shift
    # while one is already active gets a confirm prompt first. Confirming
    # does NOT create a second shift for them -- it just logs them into the
    # existing active one, since a tech can only ever be in one shift.
    if active_shifts and assoc and not is_mst:
        if confirm_dup != "1":
            from urllib.parse import quote
            err = quote("A shift is already running. Confirm you really want to join it instead of starting a new one.")
            return RedirectResponse(f"/?error={err}&confirm_needed=1", status_code=303)
        existing = active_shifts[0]
        resp = RedirectResponse("/tech", status_code=303)
        resp.set_cookie(_TECH_ASSOC_COOKIE, entered, httponly=False, samesite="lax", path="/")
        resp.set_cookie(_TECH_SHIFT_COOKIE, str(existing["id"]), httponly=False, samesite="lax", path="/")
        return resp

    # -- Same-shift-type guard -- nobody (MST included) can start a SECOND
    # instance of the SAME shift_type while one is already running. Legit
    # overlaps are always two DIFFERENT shift types (e.g. S4/S5 at handover);
    # starting the same type twice is always a mistake, not overtime. Hard
    # block, no confirm prompt. Techs never reach this line -- they're
    # already routed above.
    if any(s["shift_type"] == shift_type for s in active_shifts):
        from urllib.parse import quote
        err = quote(f"{shift_type} is already running. It can't be started twice.")
        return RedirectResponse(f"/?error={err}", status_code=303)

    # -- Assigned-shift + time-window eligibility --
    now = datetime.now()
    allowed, is_ot, elig_err = _check_shift_start_eligibility(assoc, shift_type, now)
    if not allowed:
        from urllib.parse import quote
        return RedirectResponse(f"/?error={quote(elig_err)}", status_code=303)

    opened_by_role = "tech" if (assoc and not is_mst) else "mst"

    # -- Techs land on their own dashboard, never the MST one --
    if opened_by_role == "tech":
        bound_shift_id = db.start_shift(shift_date, shift_type, lead_name,
                                        opened_by_role="tech", is_ot=False,
                                        opened_by_assoc=entered)
        resp = RedirectResponse("/tech", status_code=303)
        resp.set_cookie(_TECH_ASSOC_COOKIE, entered, httponly=False, samesite="lax", path="/")
        resp.set_cookie(_TECH_SHIFT_COOKIE, str(bound_shift_id), httponly=False, samesite="lax", path="/")
        return resp

    # ── All good -- if the associate number typed in the modal is already a
    # valid MST credential, skip the second PIN screen entirely: start the
    # shift and drop them straight into the dashboard. Otherwise (blank,
    # unrecognized, or non-MST number) fall back to asking for a PIN on the
    # next screen -- same safety net as before, just no longer the default path.
    if entered and _check_lead_credential(entered):
        bound_shift_id = db.start_shift(shift_date, shift_type, lead_name,
                                        opened_by_role="mst", is_ot=is_ot,
                                        opened_by_assoc=entered)
        resp = RedirectResponse("/lead?status=started", status_code=303)
        resp.set_cookie(_LEAD_COOKIE, _get_pin_hash(),
                        httponly=True, samesite="lax", path="/",
                        max_age=86400 * 30)  # 30 days
        resp.set_cookie(_LEAD_SHIFT_COOKIE, str(bound_shift_id),
                        httponly=True, samesite="lax", path="/")
        if _is_master_credential(entered):
            resp.set_cookie(_MGR_COOKIE, _MGR_TOKEN,
                            httponly=True, samesite="lax", path="/",
                            max_age=86400 * 30)
        return resp

    # ── Fallback -- no valid credential yet: stash the pending shift and
    # ask for a PIN on the login page (creates the shift once they sign in).
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
    shift = _resolve_my_shift(request)
    if shift and _can_manage_shift(request, shift["id"]):
        db.close_shift(shift["id"])
    return RedirectResponse("/", status_code=303)


@app.post("/shift/end", response_class=HTMLResponse)
async def shift_end(request: Request, shift_id: int = Form(...)):
    if not _is_lead_authed(request):
        return _lead_redirect("/lead")
    shift = db.get_shift(shift_id)
    if not shift or not shift.get("is_active"):
        raise HTTPException(status_code=404, detail="Shift not found or already ended")
    # Prevent a different shift's MST from ending this one
    if not _can_manage_shift(request, shift_id):
        raise HTTPException(
            status_code=403,
            detail=f"Your session is bound to a different shift. Use manager code 8972 to override.",
        )

    # Prefer the last lead-generated report; fall back to auto-building one
    last = db.get_last_report_for_shift(shift_id)
    if last:
        content = last["content"]
        # Reconstruct ReportData from stored JSON for clean email formatting
        email_data = (
            ReportData.model_validate_json(last["report_json"])
            if last.get("report_json") else None
        )
    else:
        from wo_consolidator import consolidate_entries, consolidated_to_happenings
        raw_entries   = db.get_entries(shift_id)
        co_result     = consolidate_entries(raw_entries)
        co_happenings = consolidated_to_happenings(
            co_result, wo_prefix_re=_WO_PREFIX_RE,
            cause_lookup=db.get_wo_causes(shift_id),
        )
        happenings_input = [
            HappeningInput(
                title=h["title"],
                raw_note=h["paragraph"],
            )
            for h in co_happenings
        ]
        rewritten = rewrite_all(happenings_input)
        email_data = ReportData(
            date=shift["date"],
            shift_type=shift["shift_type"],
            lead_name=shift["lead_name"],
            dc_number=DC_NUMBER,
            happenings=rewritten,
            shift_notes=db.get_shift_notes(shift_id),
        )
        content = format_email(email_data)
        db.save_report(shift_id, content, email_data.model_dump_json())

    db.close_shift(shift_id)
    loto = db.get_loto(shift_id)
    # Email is now sent manually via report UI (Send Email button)

    carry_overs = db.get_carry_over_entries(current_shift_id=shift_id)
    shift_notes = db.get_shift_notes(shift_id)

    return templates.TemplateResponse(
        request, "shift_ended.html",
        {
            "shift":       shift,
            "content":     content,
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
    pm_count  = db.get_pm_count(shift["id"])
    return templates.TemplateResponse(
        request, "tech.html",
        {
            "shift":      shift,
            "entries":    entries,
            "tech_name":  session["display_name"],
            "is_manager": session.get("is_manager", False),
            "assets":     _ASSETS,
            "oos_units":  oos_units,
            "pm_count":   pm_count,
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


@app.get("/api/active-shifts")
async def api_active_shifts():
    """JS polling endpoint -- which shift_types are currently running, so
    the homepage can lock the Start Shift button until it is safe again."""
    active = db.get_active_shifts()
    return JSONResponse({"active_types": [s["shift_type"] for s in active]})





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
    files: list[UploadFile],
    uploader_name: str = "CMMS Import",
    include_entries_oob: bool = False,
) -> HTMLResponse:
    """Shared logic for both tech and lead CMMS CSV uploads.

    Accepts one or more files (tech dashboard supports multi-select) --
    each is parsed and imported in turn, then results are combined into a
    single response so the UI shows one consolidated summary instead of
    one banner per file.
    """
    import json as _json

    shift = db.get_shift(shift_id)
    if not shift:
        return HTMLResponse(
            '<p class="text-red-500 text-sm font-semibold p-3"> Shift not found.</p>',
            status_code=404,
        )

    valid_files = [f for f in files if f.filename and f.filename.lower().endswith((".csv", ".xlsx", ".xls"))]
    bad_file_count = len(files) - len(valid_files)
    if not valid_files:
        return HTMLResponse(
            '<p class="text-red-500 text-sm font-semibold p-3"> Please upload a CSV or Excel file exported from Cetaris.</p>',
            status_code=400,
        )

    imported: list[dict] = []
    skipped = 0
    pm_wos: list[str] = []
    skipped_unchanged = 0
    cause_wos: list[str] = []  # WO numbers newly linked from a cause file

    for f in valid_files:
        content = await f.read()

        # Cause/repair-description export (WO Number + Repair Description) --
        # separate file type from the timecard export. Detected by header
        # shape so it never gets routed through parse_cetaris(). Just joins
        # by WO Number and stores the text; doesn't touch entries/pm_wos.
        if cause_lookup.looks_like_cause_file(content):
            causes = cause_lookup.parse_cause_file(content)
            for wo_number, cause_text in causes.items():
                db.upsert_wo_cause(shift_id=shift_id, wo_number=wo_number, cause_text=cause_text)
            cause_wos.extend(causes.keys())
            continue

        result: ParseResult = parse_cetaris(content)
        skipped += result.skipped
        pm_wos.extend(result.pm_wo_numbers)

        for wo in result.work_orders:
            # Fetch the existing entry for THIS tech + WO in one call --
            # reflects whatever the previous file in this same batch just
            # wrote, so remarks correctly accumulate across files too.
            existing_entry   = db.get_cmms_entry(shift_id, wo.wo_number, uploader_name)
            existing_remarks: list[str] = []
            if existing_entry:
                try:
                    existing_remarks = _json.loads(existing_entry.get("raw_remarks", "[]"))
                except (ValueError, TypeError):
                    existing_remarks = []

            merged_remarks = list(dict.fromkeys(existing_remarks + wo.remarks))

            # Nothing new — preserve the original entry and timestamp untouched
            if existing_entry and merged_remarks == existing_remarks:
                skipped_unchanged += 1
                imported.append({
                    "wo": wo.wo_number,
                    "asset": wo.asset_id,
                    "summary": existing_entry.get("issue", ""),
                    "unchanged": True,
                })
                continue

            summary = await summarize_remarks(merged_remarks)
            summary = spelling_fix.correct_spelling(summary)
            db.upsert_cmms_entry(
                shift_id=shift_id,
                wo_number=wo.wo_number,
                asset_id=wo.asset_id,
                summary=summary,
                raw_remarks_json=_json.dumps(merged_remarks),
                tech_name=uploader_name,
            )
            imported.append({"wo": wo.wo_number, "asset": wo.asset_id, "summary": summary, "unchanged": False})

    if not imported and not pm_wos and not cause_wos:
        return templates.TemplateResponse(
            request, "partials/cmms_import_result.html",
            {"imported": [], "skipped": skipped, "pm_count": 0, "cause_count": 0,
             "error": "No reliability work orders found in this file.",
             "include_entries_oob": False},
        )

    # Track PM WOs silently — deduplicated by UNIQUE constraint in DB
    for wo_num in pm_wos:
        db.upsert_pm_wo(shift_id=shift_id, wo_number=wo_num, tech_name=uploader_name)

    pm_count = db.get_pm_count(shift_id)  # total for this shift (cumulative)
    new_entries = [e for e in imported if not e.get("unchanged")]
    if new_entries:
        await _ws.broadcast({"type": "cmms_import", "count": len(new_entries), "pm_count": pm_count})

    # For tech uploads: also send an OOB swap to refresh #my-entries
    entries = db.get_entries(shift_id) if include_entries_oob else []

    resp = templates.TemplateResponse(
        request, "partials/cmms_import_result.html",
        {
            "imported":            imported,
            "skipped":             skipped,
            "skipped_unchanged":   skipped_unchanged,
            "pm_count":            pm_count,
            "cause_count":         len(set(cause_wos)),
            "error":               "",
            "include_entries_oob": include_entries_oob,
            "entries":             entries,
            "tech_name":           uploader_name,
        },
    )
    return resp


@app.post("/tech/cmms-upload", response_class=HTMLResponse)
async def tech_cmms_upload(
    request: Request,
    shift_id: int  = Form(...),
    files: list[UploadFile] = File(...),
):
    """Tech dashboard CMMS CSV upload endpoint. Accepts one or more files
    selected at once -- results are combined into a single summary."""
    assoc_code = request.cookies.get(_TECH_ASSOC_COOKIE, "")
    assoc = _ROSTER.get(assoc_code) if assoc_code else None
    if not assoc:
        return HTMLResponse(
            '<p class="text-red-500 text-sm font-semibold p-3">Session expired. Please sign in again.</p>',
            status_code=401,
        )
    uploader_name = assoc.get("display_name") or assoc.get("full_name", "CMMS Import")
    return await _process_cmms_upload(
        request, shift_id, files,
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
    _require_shift_access(request, shift_id)
    shift = db.get_shift(shift_id)
    uploader_name = f"{shift['lead_name']} (MST)" if shift else "MST Import"
    return await _process_cmms_upload(
        request, shift_id, [file],
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
                        httponly=True, samesite="lax", path="/",
                        max_age=86400 * 30)  # 30 days — survives browser/PC restarts
        # Manager cookie — set when a master credential is entered (see _MASTER_CODES)
        if _is_master_credential(pin):
            resp.set_cookie(_MGR_COOKIE, _MGR_TOKEN,
                            httponly=True, samesite="lax", path="/",
                            max_age=86400 * 30)
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
        # Credential OK -- create any pending shift
        pending_raw = request.cookies.get("__pending_shift")
        bound_shift_id = 0
        if pending_raw:
            try:
                p = json.loads(pending_raw)
                assoc = _ROSTER.get(entered)
                _, is_ot, _ = _check_shift_start_eligibility(assoc, p["shift_type"], datetime.now())
                bound_shift_id = db.start_shift(
                    p["date"], p["shift_type"], p["lead_name"],
                    opened_by_role="mst", is_ot=is_ot, opened_by_assoc=entered,
                )
            except Exception as exc:
                print(f"[login] pending shift cookie invalid: {exc}", flush=True)

        # If no pending shift, prefer an explicit shift_id carried in `next`
        # (set when the user clicked a specific shift's MST login button on
        # Home, or an overlap-window link) -- far more reliable than guessing.
        if not bound_shift_id:
            from urllib.parse import urlparse, parse_qs
            next_qs = parse_qs(urlparse(next).query)
            requested_raw = (next_qs.get("shift_id") or [None])[0]
            if requested_raw and requested_raw.isdigit():
                candidate = db.get_shift(int(requested_raw))
                if candidate and candidate.get("is_active"):
                    bound_shift_id = candidate["id"]

        # Still nothing explicit -- try matching this credential's own shift
        # by lead name, but ONLY when that's unambiguous. With 2+ shifts
        # active at once, a name match (or lack of one) is not reliable
        # enough to bind to -- leave unbound rather than guess wrong.
        if not bound_shift_id:
            assoc = _ROSTER.get(entered)
            active = db.get_active_shifts()
            if assoc and len(active) == 1:
                bound_shift_id = active[0]["id"]
            elif assoc:
                lead_display = assoc.get("display_name") or assoc.get("full_name", "")
                matches = [s for s in active if s["lead_name"] == lead_display]
                if len(matches) == 1:
                    bound_shift_id = matches[0]["id"]

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
                        httponly=True, samesite="lax", path="/",
                        max_age=86400 * 30)  # 30 days
        resp.delete_cookie("__pending_shift", path="/")
        # Bind this session to the shift — prevents cross-shift mutations
        if bound_shift_id:
            resp.set_cookie(_LEAD_SHIFT_COOKIE, str(bound_shift_id),
                            httponly=True, samesite="lax", path="/")
        else:
            resp.delete_cookie(_LEAD_SHIFT_COOKIE, path="/")
        # Manager cookie — granted when a master credential is entered (see _MASTER_CODES)
        if _is_master_credential(entered):
            resp.set_cookie(_MGR_COOKIE, _MGR_TOKEN,
                            httponly=True, samesite="lax", path="/")
        else:
            resp.delete_cookie(_MGR_COOKIE, path="/")
        return resp

    grace_shift = _find_end_shift_grace_shift(entered)
    if grace_shift:
        grace_next = "/lead?shift_id=" + str(grace_shift["id"])
        grace_html = (
            "<!DOCTYPE html><html><head>"
            "<meta http-equiv='refresh' content='0;url=" + grace_next + "' />"
            "</head><body><script>window.location.replace('" + grace_next + "');</script>"
            "</body></html>"
        )
        grace_resp = HTMLResponse(content=grace_html)
        grace_resp.set_cookie(_LEAD_COOKIE, _get_pin_hash(),
                               httponly=True, samesite="lax", path="/",
                               max_age=86400)
        grace_resp.set_cookie(_LEAD_SHIFT_COOKIE, str(grace_shift["id"]),
                               httponly=True, samesite="lax", path="/")
        grace_resp.delete_cookie(_MGR_COOKIE, path="/")
        return grace_resp

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
    # Which shift are we showing? Explicit request > this session's own bound
    # shift > (only when truly unambiguous) the single active shift. Then the
    # access check is what actually stops an MST from viewing a shift that
    # isn't theirs -- an explicit shift_id that fails the check sends them
    # home instead of silently rendering someone else's shift.
    bound_id  = _get_lead_shift_id(request)
    target_id = shift_id or bound_id or (shifts[0]["id"] if len(shifts) == 1 else 0)
    shift = next((s for s in shifts if s["id"] == target_id), None)
    if not shift or not _can_manage_shift(request, target_id):
        return RedirectResponse("/?pick_shift=1", status_code=303)
    entries = db.get_entries(shift["id"])
    carry_overs = db.get_carry_over_entries(current_shift_id=shift["id"])
    shift_notes = db.get_shift_notes(shift["id"])
    response = templates.TemplateResponse(
        request, "lead.html",
        {"shift": shift, "shifts": shifts, "entries": entries, "status": status,
         "assets": _ASSETS, "carry_overs": carry_overs, "shift_notes": shift_notes,
         "shift_id": shift["id"],
         "loto": db.get_loto(shift["id"]),
         "pm_files": db.get_pm_files(shift["id"]),
         "pending_pms": db.get_pending_pms(shift["id"]),
         "v3_operations": bool(request.cookies.get("ind3_v3_mst_session"))}
    )
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response


@app.get("/lead/tech-participation", response_class=HTMLResponse)
async def lead_tech_participation(request: Request, shift_id: int = 0):
    if not _is_lead_authed(request):
        return HTMLResponse('')
    shift = db.get_shift(shift_id) if shift_id else db.get_active_shift()
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


@app.get("/analytics", response_class=HTMLResponse)
async def analytics_dashboard(request: Request):
    if not _is_lead_authed(request):
        return _lead_redirect("/analytics")
    import json as _json_mod
    data = db.get_analytics()
    return templates.TemplateResponse(
        request, "analytics.html",
        {"data": data, "data_json": _json_mod.dumps(data)}
    )


@app.get("/guide", response_class=HTMLResponse)
async def user_guide(request: Request):
    """Legacy URL -- the standalone guide page was replaced by the unified
    screenshot walkthrough. Redirect so old bookmarks/QR codes keep working."""
    return RedirectResponse("/walkthrough/", status_code=303)


@app.get("/tech-docs", response_class=HTMLResponse)
async def tech_docs(request: Request):
    """Legacy URL -- technical docs are now folded into the walkthrough page
    (which links out to the Markdown docs in docs/ for deep technical detail)."""
    return RedirectResponse("/walkthrough/", status_code=303)


@app.get("/guide/download")
async def download_guide(request: Request):
    """Legacy URL -- redirect to the walkthrough, which has no download
    step since it's already a self-contained static page."""
    return RedirectResponse("/walkthrough/", status_code=303)



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
async def lead_asset_stats(request: Request, shift_id: int = 0):
    """HTMX polling endpoint — asset frequency badges + chart data."""
    if not _is_lead_authed(request):
        return HTMLResponse('')
    shift = db.get_shift(shift_id) if shift_id else db.get_active_shift()
    if not shift:
        return HTMLResponse('<p class="text-gray-400 italic text-sm p-2">No active shift.</p>')
    stats = db.get_asset_stats(shift["id"])
    recurring = db.get_recurring_assets(days=7, min_reports=3)
    return templates.TemplateResponse(
        request, "partials/asset_stats.html", {"stats": stats, "recurring": recurring}
    )


@app.get("/lead/feed", response_class=HTMLResponse)
async def lead_feed(request: Request, shift_id: int = 0):
    """HTMX polling endpoint – returns live entry feed partial.

    Accepts shift_id so each MST always sees their own shift, even during
    overlap windows when two shifts are running simultaneously.
    Only sends HX-Refresh when the requested shift is no longer active
    (i.e. it has been ended), not on a shift-id mismatch.
    """
    if not _is_lead_authed(request):
        return HTMLResponse('')

    shift = db.get_shift(shift_id) if shift_id else db.get_active_shift()
    if not shift:
        # Shift was ended — reload so the page transitions to ended state
        return HTMLResponse(
            '<p class="text-gray-400 italic text-sm">Shift ended.</p>',
            headers={"HX-Refresh": "true"}
        )
    # If the specific shift was ended, reload cleanly
    if not shift.get("is_active"):
        return HTMLResponse("", headers={"HX-Refresh": "true"})

    entries = db.get_entries(shift["id"])
    return templates.TemplateResponse(
        request, "partials/live_feed.html", {"entries": entries}
    )


# ── Carry-over panel partial + actions ──────────────────────────────────────

@app.get("/lead/carry-overs", response_class=HTMLResponse)
async def lead_carry_overs_panel(request: Request, shift_id: int):
    """Returns the full carry-over panel so HTMX can refresh it after actions."""
    if not _is_lead_authed(request):
        return HTMLResponse("")
    carry_overs = db.get_carry_over_entries(current_shift_id=shift_id)
    return templates.TemplateResponse(
        request, "partials/carry_overs_panel.html",
        {"carry_overs": carry_overs, "shift_id": shift_id},
    )


@app.post("/lead/carry-over/{entry_id}/dismiss", response_class=HTMLResponse)
async def dismiss_carry_over(
    request: Request,
    entry_id: int,
    shift_id: int = Form(...),
):
    """Clear the carry-over flag — item is resolved, stop showing it."""
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    _require_shift_access(request, shift_id)
    db.set_carry_over(entry_id, False)
    carry_overs = db.get_carry_over_entries(current_shift_id=shift_id)
    return templates.TemplateResponse(
        request, "partials/carry_overs_panel.html",
        {"carry_overs": carry_overs, "shift_id": shift_id},
    )


@app.post("/lead/carry-over/{entry_id}/adopt", response_class=HTMLResponse)
async def adopt_carry_over(
    request: Request,
    entry_id: int,
    shift_id: int = Form(...),
):
    """Copy this carry-over into the current shift as a new entry, then clear the flag.
    The MST can then manage it (carry it over again, exclude it, etc.) from the live feed.
    """
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    _require_shift_access(request, shift_id)
    source = db.get_entry(entry_id)
    if source:
        lead_name = db.get_shift(shift_id)["lead_name"] if db.get_shift(shift_id) else "MST"
        db.add_entry(
            shift_id=shift_id,
            tech_name=f"{lead_name} (adopted)",
            unit=source["unit"],
            issue=source["issue"],
            action=source.get("action", ""),
            result=source.get("result", ""),
            priority=source.get("priority", "monitor"),
        )
        db.set_carry_over(entry_id, False)
        await _ws.broadcast({"type": "new_entry", "tech": lead_name,
                             "unit": source["unit"], "priority": source.get("priority", "monitor")})
    carry_overs = db.get_carry_over_entries(current_shift_id=shift_id)
    return templates.TemplateResponse(
        request, "partials/carry_overs_panel.html",
        {"carry_overs": carry_overs, "shift_id": shift_id},
    )


# ── Carry-over toggle (live-feed button) ────────────────────────────────────

@app.post("/lead/entry/{entry_id}/carry-over", response_class=HTMLResponse)
async def toggle_carry_over(
    request: Request,
    entry_id: int,
    state: int = Form(1),
):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    entry = db.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404)
    _require_shift_access(request, entry["shift_id"])
    db.set_carry_over(entry_id, bool(state))
    return HTMLResponse("")


# ── MST entry controls: exclude from report + hard delete ────────────────────────────

@app.post("/lead/entry/{entry_id}/exclude", response_class=HTMLResponse)
async def lead_exclude_entry(
    request: Request,
    entry_id: int,
    excluded: int = Form(1),
):
    """Toggle exclusion of an entry from the generated report."""
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    entry = db.get_entry(entry_id)
    if not entry:
        return HTMLResponse("")
    _require_shift_access(request, entry["shift_id"])
    db.set_entry_excluded(entry_id, bool(excluded))
    # Re-render just this entry row so the button + styling update in place
    entries = db.get_entries(entry["shift_id"])
    entry = next((e for e in entries if e["id"] == entry_id), None)
    if not entry:
        return HTMLResponse("")  # deleted or wrong shift
    return templates.TemplateResponse(
        request, "partials/live_feed_row.html", {"e": entry}
    )


@app.delete("/lead/entry/{entry_id}", response_class=HTMLResponse)
async def lead_delete_entry(
    request: Request,
    entry_id: int,
):
    """Hard-delete an entry from the shift (MST only)."""
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    entry = db.get_entry(entry_id)
    if not entry:
        return HTMLResponse("")
    _require_shift_access(request, entry["shift_id"])
    db.delete_entry(entry_id)
    return HTMLResponse("")  # target row gets removed by HTMX hx-swap outerHTML


# ── MST inline entry edit ────────────────────────────────────────────────────────

@app.get("/lead/entry/{entry_id}/edit-form", response_class=HTMLResponse)
async def lead_entry_edit_form(request: Request, entry_id: int):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    entry = db.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "partials/live_feed_row_edit.html", {"e": entry}
    )


@app.get("/lead/entry/{entry_id}/edit-form/cancel", response_class=HTMLResponse)
async def lead_entry_edit_cancel(request: Request, entry_id: int):
    """Cancel edit — re-render the normal read-only row."""
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    entry = db.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "partials/live_feed_row.html", {"e": entry}
    )


@app.post("/lead/entry/{entry_id}/update", response_class=HTMLResponse)
async def lead_entry_update(
    request: Request,
    entry_id: int,
    issue: str = Form(...),
):
    """MST updates the issue text of an entry in place."""
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    # Fetch the entry directly — never rely on get_active_shift() here
    # because the entry may belong to any shift (overlap window, etc.)
    entry = db.get_entry(entry_id)
    if not entry:
        raise HTTPException(status_code=404)
    _require_shift_access(request, entry["shift_id"])
    db.update_entry_issue(entry_id, issue.strip())
    entry = db.get_entry(entry_id)
    return templates.TemplateResponse(
        request, "partials/live_feed_row.html", {"e": entry}
    )



# ── Shift notes ───────────────────────────────────────────────────────────────────────

@app.post("/lead/shift-notes", response_class=HTMLResponse)
async def save_shift_notes(
    request: Request,
    shift_id: int = Form(...),
    notes: str = Form(""),
):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    _require_shift_access(request, shift_id)
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
        fresh = assoc_mod.load_associates()   # read Excel
        db.force_reseed_associates(fresh)     # upsert DB
        _ROSTER = db.associates_to_roster()   # reload in-memory
        count = len(_ROSTER) - 1             # subtract manager entry
        msg_html = (
            f'<span class="text-green-600 text-xs font-semibold">'
            f'&#10003; Synced {count} associates from Drax</span>'
        )
        # HX-Trigger tells HTMX to fire a custom event so #assoc-table re-fetches
        return HTMLResponse(
            msg_html,
            headers={"HX-Trigger": "rosterReloaded"},
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
        "email_to":          db.get_setting("email_to", ""),
        "email_from_name":   db.get_setting("email_from_name", "IND3 Shift Passdown"),
        "ms_client_id":      db.get_setting("ms_client_id", ""),
        "outlook_ready":     _outlook_ready(),
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
    msg = "Webhook saved — Teams notifications active" if url else "Webhook cleared — Teams notifications off"
    return RedirectResponse(f"/admin?msg={msg.replace(' ', '+')}", status_code=303)


# ── LOTO verification ──────────────────────────────────────────────────────

@app.get("/lead/pm-files", response_class=HTMLResponse)
async def lead_pm_files_list(request: Request, shift_id: int = 0):
    if not _is_lead_authed(request):
        return HTMLResponse("")
    return HTMLResponse(_render_pm_section(shift_id))


@app.post("/lead/loto", response_class=HTMLResponse)
async def lead_save_loto(
    request:       Request,
    shift_id:      int = Form(...),
    loto_verified: str = Form(""),
    missing_keys:  str = Form(""),
):
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)

    _require_shift_access(request, shift_id)
    # If any missing keys recorded — LOTO was checked (just missing some)
    keys_clean = ", ".join(k.strip() for k in missing_keys.split(",") if k.strip())
    verified   = bool(keys_clean) or loto_verified.lower() in ("on", "true", "1", "yes")
    db.save_loto(shift_id, verified, keys_clean)
    return HTMLResponse(_loto_status_html(verified, keys_clean, shift_id))


@app.post("/lead/loto/remove-key", response_class=HTMLResponse)
async def lead_loto_remove_key(
    request:        Request,
    shift_id:       int = Form(...),
    key_to_remove:  str = Form(...),
):
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    _require_shift_access(request, shift_id)
    loto = db.get_loto(shift_id)
    existing = loto.get("loto_missing_keys", "")
    keys     = [k.strip() for k in existing.split(",") if k.strip()]
    keys     = [k for k in keys if k.lower() != key_to_remove.strip().lower()]
    keys_clean = ", ".join(keys)
    # Keep verified=True even after removing — they still did the check
    db.save_loto(shift_id, loto.get("loto_verified", True), keys_clean)
    return HTMLResponse(_loto_status_html(True, keys_clean, shift_id))


def _loto_status_html(verified: bool, missing_keys: str, shift_id: int) -> str:
    """Shared HTML for the loto-status div + OOB badge update."""
    keys = [k.strip() for k in missing_keys.split(",") if k.strip()]

    if verified:
        if keys:
            chips = "".join(
                f'<form method="POST" action="/lead/loto/remove-key" '
                f'hx-post="/lead/loto/remove-key" hx-target="#loto-status" hx-swap="innerHTML" '
                f'style="display:inline;">'
                f'<input type="hidden" name="shift_id" value="{shift_id}">'
                f'<input type="hidden" name="key_to_remove" value="{k}">'
                f'<button type="submit" title="Key found — remove" '
                f'style="display:inline-flex;align-items:center;gap:4px;margin:2px;'
                f'padding:3px 8px;background:#fef2f2;border:1px solid #fca5a5;'
                f'border-radius:999px;font-size:11px;font-weight:700;color:#b91c1c;'
                f'cursor:pointer;">'
                f'{k} &times;</button></form>'
                for k in keys
            )
            status_html = (
                '<p class="font-bold text-sm text-green-700">&#10003; LOTO Verified — keys missing:</p>'
                f'<div class="flex flex-wrap gap-1 mt-1">{chips}</div>'
                '<p class="text-xs text-gray-400 mt-1">Click a key to remove it once found.</p>'
            )
            badge_label, badge_cls = "&#10003; Verified", "bg-green-100 text-green-700"
        else:
            status_html = '<p class="font-bold text-sm text-green-700">&#10003; All LOTO Keys Verified</p>'
            badge_label, badge_cls = "&#10003; Verified", "bg-green-100 text-green-700"
    else:
        status_html = '<p class="text-gray-400 italic text-xs">Not yet verified for this shift.</p>'
        badge_label, badge_cls = "Pending", "bg-yellow-100 text-yellow-700"

    oob_badge = (
        f'<span id="loto-badge" hx-swap-oob="true" '
        f'class="ml-auto text-xs font-black px-2 py-0.5 rounded-full {badge_cls}">{badge_label}</span>'
    )
    return status_html + oob_badge


# ── PM file upload ──────────────────────────────────────────────────────────

PM_UPLOAD_DIR = Path("pm_uploads")
PM_UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_PM_EXTS = {".pdf", ".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg"}


@app.post("/lead/pm-upload", response_class=HTMLResponse)
async def lead_pm_upload(
    request:  Request,
    shift_id: int  = Form(...),
    pm_file:  UploadFile = File(...),
):
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    _require_shift_access(request, shift_id)
    ext = Path(pm_file.filename or "").suffix.lower()
    if ext not in ALLOWED_PM_EXTS:
        return HTMLResponse(
            f'<p class="text-red-600 text-xs font-bold">&#10060; File type not allowed. Use PDF, Excel, CSV or image.</p>'
        )
    stored = f"{shift_id}_{uuid.uuid4().hex[:8]}{ext}"
    dest   = PM_UPLOAD_DIR / stored
    data   = await pm_file.read()
    dest.write_bytes(data)
    session = _get_lead_session_name(request)
    db.save_pm_file(shift_id, pm_file.filename or stored, stored, session or "lead")

    # Try to parse as Cetaris PM pending report
    parsed_pms: list = []
    if ext in (".xlsx", ".xls", ".csv"):
        try:
            rows = pm_pending_parser.parse_pm_pending(data, pm_file.filename or "")
            if rows:
                db.save_pending_pms(shift_id, [{
                    "wo_number":    r.wo_number,
                    "wo_date":      r.wo_date,
                    "pm_due_date":  r.pm_due_date,
                    "promise_date": r.promise_date,
                    "asset_num":    r.asset_num,
                } for r in rows])
                parsed_pms = rows
                print(f"[pm] parsed {len(rows)} pending PM rows from {pm_file.filename}", flush=True)
        except Exception as exc:
            print(f"[pm] parse failed: {exc}", flush=True)

    files = db.get_pm_files(shift_id)
    return HTMLResponse(_render_pm_section(shift_id))


@app.get("/lead/pm-file/{file_id}/download")
async def lead_pm_download(request: Request, file_id: int):
    if not _is_lead_authed(request):
        return RedirectResponse("/lead", status_code=303)
    import sqlite3 as _sql
    with _sql.connect(db.get_db_path()) as con:
        con.row_factory = _sql.Row
        row = con.execute("SELECT * FROM pm_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(404, "File not found")
    path = PM_UPLOAD_DIR / row["stored_name"]
    if not path.exists():
        raise HTTPException(404, "File missing from disk")
    from fastapi.responses import FileResponse
    return FileResponse(path, filename=row["filename"], media_type="application/octet-stream")


@app.post("/lead/pm-file/{file_id}/delete", response_class=HTMLResponse)
async def lead_pm_delete(request: Request, file_id: int, shift_id: int = Form(...)):
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    _require_shift_access(request, shift_id)
    stored = db.delete_pm_file(file_id)
    if stored:
        p = PM_UPLOAD_DIR / stored
        if p.exists():
            p.unlink()
        # Also wipe the PMs that were extracted from this file
        db.clear_pending_pms(shift_id)
    return HTMLResponse(_render_pm_section(shift_id))


def _render_pm_section(shift_id: int) -> str:
    """Full #pm-file-list outer div — used for both init-load and post-upload outerHTML swap."""
    files = db.get_pm_files(shift_id)
    rows  = db.get_pending_pms(shift_id)

    # File chips
    if not files:
        file_html = '<p class="text-gray-400 text-xs italic">No files uploaded yet.</p>'
    else:
        chips = "".join(
            f'<div class="flex items-center justify-between gap-2 py-1.5 border-b border-gray-100 last:border-0">'
            f'<a href="/lead/pm-file/{f["id"]}/download" class="text-xs text-wm-blue hover:underline truncate max-w-[70%]" title="{f["filename"]}">'
            f'&#128196; {f["filename"]}</a>'
            f'<form method="POST" action="/lead/pm-file/{f["id"]}/delete">'
            f'<input type="hidden" name="shift_id" value="{f["shift_id"]}">'
            f'<button class="text-red-400 hover:text-red-600 text-xs font-bold">&#10005;</button>'
            f'</form></div>'
            for f in files
        )
        file_html = f'<div class="flex flex-col">{chips}</div>'

    # PM table
    if not rows:
        table_html = ""
    else:
        from pm_table_style import pm_zebra_hex
        th = "px-3 py-2 text-left text-xs font-bold text-white uppercase bg-wm-blue"
        td = "px-3 py-2 text-xs text-gray-800"
        hdr = (
            f'<tr><th class="{th}">WO #</th><th class="{th}">Asset</th>'
            f'<th class="{th}">PM Due</th><th class="{th}">Promise</th>'
            f'<th class="{th}">WO Date</th></tr>'
        )
        body = "".join(
            f'<tr style="background:#{pm_zebra_hex(i)};" class="border-b border-gray-100 last:border-0">'
            f'<td class="{td} font-mono">{r["wo_number"]}</td>'
            f'<td class="{td} font-semibold">{r["asset_num"]}</td>'
            f'<td class="{td} font-bold">{r["pm_due_date"]}</td>'
            f'<td class="{td}">{r["promise_date"]}</td>'
            f'<td class="{td} text-gray-500">{r["wo_date"]}</td>'
            f'</tr>'
            for i, r in enumerate(rows)
        )
        table_html = (
            f'<div class="mt-3 overflow-x-auto rounded-lg border border-gray-200">'
            f'<table class="w-full border-collapse"><thead>{hdr}</thead>'
            f'<tbody>{body}</tbody></table></div>'
        )

    return (
        f'<div id="pm-file-list" class="min-h-[2rem] mb-3">'
        + file_html
        + table_html
        + '</div>'
    )


def _render_pm_file_list(files: list[dict]) -> str:
    if not files:
        return '<p class="text-gray-400 text-xs italic">No files uploaded yet.</p>'
    rows = "".join(
        f'<div class="flex items-center justify-between gap-2 py-1.5 border-b border-gray-100 last:border-0">'
        f'<a href="/lead/pm-file/{f["id"]}/download" class="text-xs text-wm-blue hover:underline truncate max-w-[70%]" title="{f["filename"]}">'
        f'&#128196; {f["filename"]}</a>'
        f'<form method="POST" action="/lead/pm-file/{f["id"]}/delete" class="shrink-0">'
        f'<input type="hidden" name="shift_id" value="{f["shift_id"]}">'
        f'<button class="text-red-400 hover:text-red-600 text-xs font-bold">&#10005;</button>'
        f'</form></div>'
        for f in files
    )
    return f'<div class="flex flex-col">{rows}</div>'


def _get_lead_session_name(request: Request) -> str:
    """Best-effort: return the lead's display name from their shift cookie."""
    raw = request.cookies.get(_LEAD_SHIFT_COOKIE, "")
    if raw.isdigit():
        shift = db.get_shift(int(raw))
        if shift:
            return shift.get("lead_name", "")
    return ""


# ── Shift Happenings file upload ────────────────────────────────────────────────────
HAPPENINGS_UPLOAD_DIR = Path("happenings_uploads")
HAPPENINGS_UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_HAPPENINGS_EXTS = {".pdf", ".xlsx", ".xls", ".csv", ".png", ".jpg", ".jpeg", ".docx", ".txt"}


@app.get("/lead/happenings-files", response_class=HTMLResponse)
async def lead_happenings_files_list(request: Request, shift_id: int = 0):
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    files = db.get_happenings_files(shift_id)
    return HTMLResponse(_render_happenings_file_list(shift_id, files))


@app.post("/lead/happenings-upload", response_class=HTMLResponse)
async def lead_happenings_upload(
    request:          Request,
    shift_id:         int        = Form(...),
    happenings_file:  UploadFile = File(...),
):
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    _require_shift_access(request, shift_id)
    ext = Path(happenings_file.filename or "").suffix.lower()
    if ext not in ALLOWED_HAPPENINGS_EXTS:
        return HTMLResponse(
            '<p class="text-red-600 text-xs font-bold">&#10060; File type not allowed.</p>'
        )
    stored = f"{shift_id}_{uuid.uuid4().hex[:8]}{ext}"
    dest   = HAPPENINGS_UPLOAD_DIR / stored
    dest.write_bytes(await happenings_file.read())
    session = _get_lead_session_name(request)
    db.save_happenings_file(shift_id, happenings_file.filename or stored, stored, session or "lead")
    files = db.get_happenings_files(shift_id)
    return HTMLResponse(_render_happenings_file_list(shift_id, files))


@app.get("/lead/happenings-file/{file_id}/download")
async def lead_happenings_file_download(request: Request, file_id: int):
    if not _is_lead_authed(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    files = db.get_happenings_files(0)  # fetch all, then filter
    # look up across any shift
    with __import__('db')._conn() as c:
        row = c.execute("SELECT * FROM happenings_files WHERE id=?", (file_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="File not found")
    path = HAPPENINGS_UPLOAD_DIR / row["stored_name"]
    if not path.exists():
        raise HTTPException(status_code=404, detail="File missing from disk")
    return FileResponse(path, filename=row["filename"])


@app.post("/lead/happenings-file/{file_id}/delete", response_class=HTMLResponse)
async def lead_happenings_file_delete(request: Request, file_id: int, shift_id: int = Form(...)):
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    _require_shift_access(request, shift_id)
    stored = db.delete_happenings_file(file_id)
    if stored:
        p = HAPPENINGS_UPLOAD_DIR / stored
        if p.exists():
            p.unlink()
    files = db.get_happenings_files(shift_id)
    return HTMLResponse(_render_happenings_file_list(shift_id, files))


def _render_happenings_file_list(shift_id: int, files: list[dict]) -> str:
    """Outer #happenings-file-list div — used for init-load and post-upload outerHTML swap."""
    if not files:
        chip_html = '<p class="text-slate-400 text-xs italic">No files uploaded yet.</p>'
    else:
        chip_html = '<div class="flex flex-col">'
        for f in files:
            chip_html += (
                f'<div class="flex items-center justify-between gap-2 py-1.5'
                f' border-b border-slate-100 last:border-0">'
                f'<a href="/lead/happenings-file/{f["id"]}/download"'
                f' class="text-xs text-wm-blue hover:underline truncate max-w-[70%]"'
                f' title="{f["filename"]}">&#128196; {f["filename"]}</a>'
                f'<form method="POST" action="/lead/happenings-file/{f["id"]}/delete"'
                f' hx-post="/lead/happenings-file/{f["id"]}/delete"'
                f' hx-target="#happenings-file-list" hx-swap="outerHTML">'
                f'<input type="hidden" name="shift_id" value="{f["shift_id"]}">'
                f'<button class="text-red-400 hover:text-red-600 text-xs font-bold">&#10005;</button>'
                f'</form></div>'
            )
        chip_html += '</div>'
    return (
        f'<div id="happenings-file-list" class="min-h-[1.5rem] mb-2">'
        + chip_html
        + '</div>'
    )


@app.post("/admin/email-settings")
async def admin_save_email_settings(
    request:         Request,
    email_from_name: str = Form("IND3 Shift Passdown"),
    email_to:        str = Form(""),
    ms_client_id:    str = Form(""),
):
    if not _is_lead_authed(request):
        return _lead_redirect("/admin")
    db.set_setting("email_from_name", email_from_name.strip() or "IND3 Shift Passdown")
    db.set_setting("email_to",        email_to.strip())
    db.set_setting("ms_client_id",    ms_client_id.strip())
    return RedirectResponse("/admin?msg=Email+settings+saved", status_code=303)


# ── Email preview + manual send ────────────────────────────────────────────────────

@app.get("/report/{report_id}/email-preview", response_class=HTMLResponse)
async def report_email_preview(request: Request, report_id: int):
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    rec = db.get_report(report_id)
    if not rec:
        return HTMLResponse("Report not found", status_code=404)
    shift_id = rec["shift_id"]
    shift    = db.get_shift(shift_id)
    loto     = db.get_loto(shift_id)
    email_data = (
        ReportData.model_validate_json(rec["report_json"])
        if rec.get("report_json") else None
    )
    html_body = format_email_passdown_html(
        email_data,
        loto_missing=loto.get("loto_missing_keys", ""),
        pending_pms=db.get_pending_pms(shift_id),
        loto_verified=bool(loto.get("loto_verified", 0)),
    ) if email_data else rec["content"]

    subj = (
        f"{shift['shift_type']} Passdown"
    ) if shift else "Passdown"

    return templates.TemplateResponse(
        request, "partials/email_preview_modal.html",
        {"report_id": report_id, "subject": subj,
         "html_body": html_body,
         "email_to": db.get_setting("email_to", "").strip()},
    )


@app.post("/report/{report_id}/open-in-outlook", response_class=HTMLResponse)
async def report_open_in_outlook(
    request:   Request,
    report_id: int,
    subject:   str = Form(""),
    html_body: str = Form(""),
):
    """Open an Outlook compose window pre-filled with the passdown email.

    The lead’s saved signature is preserved automatically below the content.
    They review, optionally edit, then hit Send from their own Outlook account.
    """
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    if not _outlook_ready():
        return HTMLResponse(
            '<span style="color:#c00;font-weight:700;">&#10060; Outlook not available on this PC.</span>',
            status_code=503,
        )

    rec = db.get_report(report_id)
    if not rec:
        return HTMLResponse("Report not found", status_code=404)

    to_raw   = db.get_setting("email_to", "").strip()
    to_addrs = [a.strip() for a in to_raw.split(",") if a.strip()]
    reply_to = db.get_setting("email_reply_to", "").strip()

    # If caller sent an edited body use it; otherwise regenerate from saved data
    if html_body.strip():
        body = html_body
    else:
        data = ReportData.model_validate_json(rec["report_json"]) if rec.get("report_json") else None
        loto = db.get_loto(rec["shift_id"])
        pms  = db.get_pending_pms(rec["shift_id"])
        body = format_email_passdown_html(
            data,
            loto_missing=loto.get("loto_missing_keys", ""),
            pending_pms=pms or None,
            loto_verified=bool(loto.get("loto_verified", 0)),
        ) if data else rec.get("content", "")

    try:
        await outlook_sender.display_email(
            to_addrs=to_addrs,
            subject=subject or "Passdown",
            body=body,
            reply_to=reply_to,
        )
        return HTMLResponse(
            '<span style="color:#2a8703;font-weight:700;">'
            '&#9989; Outlook is open — review, add your signature, then click Send!'
            '</span>'
        )
    except Exception as exc:
        return HTMLResponse(
            f'<span style="color:#c00;font-weight:700;">&#10060; {exc}</span>',
            status_code=500,
        )


# ── Compose in Outlook via OWA or Graph API draft ─────────────────────────────


@app.get("/report/{report_id}/email-html", response_class=HTMLResponse)
async def report_email_html(request: Request, report_id: int):
    """Return the raw HTML email body for clipboard copy.
    Used by the one-click button to pre-copy formatted content
    before opening OWA, so the lead can paste it with Ctrl+V.
    """
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    rec = db.get_report(report_id)
    if not rec or not rec.get("report_json"):
        return HTMLResponse("Not found", status_code=404)
    data      = ReportData.model_validate_json(rec["report_json"])
    loto      = db.get_loto(rec["shift_id"])
    pms       = db.get_pending_pms(rec["shift_id"])
    body_html = format_email_passdown_html(
        data,
        loto_missing=loto.get("loto_missing_keys", ""),
        pending_pms=pms or None,
        loto_verified=bool(loto.get("loto_verified", 0)),
    )
    return HTMLResponse(body_html)


@app.get("/report/{report_id}/compose-outlook")
async def report_compose_outlook_get(request: Request, report_id: int):
    """GET — one-click button triggers this as a download.
    Returns a .eml file (X-Unsent:1) with full HTML body + lead signature.
    Desktop Outlook opens it in compose mode. Lead hits Send.
    """
    return await _build_eml_response(report_id, subject="", html_body="")


@app.post("/report/{report_id}/compose-outlook")
async def report_compose_outlook(
    request:   Request,
    report_id: int,
    subject:   str = Form(""),
    html_body: str = Form(""),
):
    """POST — preview modal uses this when the lead has edited the body."""
    return await _build_eml_response(report_id, subject=subject, html_body=html_body)


async def _build_eml_response(report_id: int, subject: str, html_body: str) -> Response:
    """Build a .eml (X-Unsent:1) with full HTML body + embedded lead signature.
    Desktop Outlook opens it directly in compose mode.
    """
    rec = db.get_report(report_id)
    if not rec:
        return Response("Report not found", status_code=404, media_type="text/plain")

    to_addrs  = db.get_setting("email_to", "").strip()
    lead_name = ""

    # ── Subject ──
    if not subject.strip() and rec.get("report_json"):
        data      = ReportData.model_validate_json(rec["report_json"])
        lead_name = data.lead_name
        subject   = (
            f"{data.shift_type} Passdown"
        )

    # ── HTML body ──
    if html_body.strip():
        body_html = html_body
        if not lead_name and rec.get("report_json"):
            lead_name = ReportData.model_validate_json(rec["report_json"]).lead_name
    elif rec.get("report_json"):
        data      = ReportData.model_validate_json(rec["report_json"])
        lead_name = data.lead_name
        loto      = db.get_loto(rec["shift_id"])
        pms       = db.get_pending_pms(rec["shift_id"])
        body_html = format_email_passdown_html(
            data,
            loto_missing=loto.get("loto_missing_keys", ""),
            pending_pms=pms or None,
            loto_verified=bool(loto.get("loto_verified", 0)),
        )
    else:
        body_html = f"<pre>{rec.get('content', '')}</pre>"

    # Append the Next Gen signature; keep the existing passdown HTML untouched.
    signature_shift = (
        ReportData.model_validate_json(rec["report_json"]).shift_type
        if rec.get("report_json") else ""
    )
    body_html = _sigs.append_signature(body_html, lead_name, signature_shift)

    # ── Build .eml ──
    import base64 as _b64
    body_b64        = _b64.b64encode(body_html.encode("utf-8")).decode("ascii")
    body_b64_folded = "\r\n".join(body_b64[i:i+76] for i in range(0, len(body_b64), 76))
    eml = (
        f"X-Unsent: 1\r\n"
        f"To: {to_addrs}\r\n"
        f"Subject: {subject}\r\n"
        f"MIME-Version: 1.0\r\n"
        f'Content-Type: text/html; charset="utf-8"\r\n'
        f"Content-Transfer-Encoding: base64\r\n"
        f"\r\n"
        f"{body_b64_folded}\r\n"
    )
    shift_label = ""
    if rec.get("report_json"):
        d = ReportData.model_validate_json(rec["report_json"])
        shift_label = f"{d.shift_type}-{d.date}-"
    filename = f"passdown-{shift_label}{report_id}.eml"
    return Response(
        content=_sigs.embed_spark_in_eml(eml.encode("utf-8")),
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _html_to_plain(html: str) -> str:
    """Convert HTML email to readable plain text matching the passdown format."""
    import re as _re
    import html as _hl

    t = html
    # <br> → newline
    t = _re.sub(r'<br\s*/?>', '\n', t, flags=_re.I)
    # Section headings (strong/b on their own line) → UPPERCASE + dashes
    t = _re.sub(
        r'<(strong|b)>([^<]{4,60})</(strong|b)>',
        lambda m: f'\n{m.group(2).upper()}\n{"-"*40}',
        t, flags=_re.I
    )
    # List items → bullet
    t = _re.sub(r'<li[^>]*>', '\n\u2022 ', t, flags=_re.I)
    # Table header cells → padded columns
    t = _re.sub(r'<th[^>]*>', '  ', t, flags=_re.I)
    # Table data cells → padded columns  
    t = _re.sub(r'<td[^>]*>', '  ', t, flags=_re.I)
    # Table rows → newline
    t = _re.sub(r'<tr[^>]*>', '\n', t, flags=_re.I)
    # Block elements → newline
    t = _re.sub(r'</(p|div|h[1-6]|tr|li|ul|ol)>', '\n', t, flags=_re.I)
    # HR → separator
    t = _re.sub(r'<hr[^>]*>', '\n' + '-'*40 + '\n', t, flags=_re.I)
    # Strip all remaining tags
    t = _re.sub(r'<[^>]+>', '', t)
    # Decode HTML entities (&amp; &#10003; etc.)
    t = _hl.unescape(t)
    # Collapse multiple spaces on same line
    t = _re.sub(r'[ \t]{2,}', ' ', t)
    # Max two consecutive newlines
    t = _re.sub(r'\n{3,}', '\n\n', t)
    return t.strip()


@app.get("/outlook-draft-callback")
async def outlook_draft_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Kept as a stub in case the old OAuth flow is ever revisited."""
    return HTMLResponse(
        "<p style='font-family:sans-serif;padding:40px;'>'"
        "OAuth flow is disabled. Close this tab.</p>"
    )


@app.post("/report/{report_id}/send-email", response_class=HTMLResponse)
async def report_send_email(
    request:   Request,
    report_id: int,
    subject:   str  = Form(""),
    html_body: str  = Form(""),
):
    if not _is_lead_authed(request):
        return HTMLResponse("Unauthorized", status_code=401)
    if db.is_sandboxed():
        return HTMLResponse(
            '<p class="text-amber-700 font-bold text-sm">&#129514; Dev/Test Mode &mdash; email sending is disabled here so nothing goes out to real recipients.</p>'
        )
    if not db.get_setting("email_to", "").strip():
        return HTMLResponse(
            '<p class="text-red-600 font-bold text-sm">&#10060; No recipients configured — add them in Admin → Email Settings.</p>'
        )
    if not _outlook_ready():
        return HTMLResponse(
            '<p class="text-red-600 font-bold text-sm">&#10060; Outlook is not running. Open Outlook and try again.</p>'
        )
    to_raw    = db.get_setting("email_to", "").strip()
    to_list   = [a.strip() for a in to_raw.split(",") if a.strip()]
    from_name = db.get_setting("email_from_name", "IND3 Shift Passdown").strip()
    reply_to  = db.get_setting("email_reply_to", "").strip() or (to_list[0] if to_list else "")
    try:
        await outlook_sender.send_email(
            to_list, subject or "IND3 Passdown",
            html_body, from_name=from_name, reply_to=reply_to,
        )
        return HTMLResponse(
            '<p class="text-green-700 font-bold text-sm">&#10003; Email sent successfully!</p>'
        )
    except Exception as exc:
        return HTMLResponse(
            f'<p class="text-red-600 font-bold text-sm">&#10060; Send failed: {exc}</p>'
        )


@app.get("/report/{report_id}/print", response_class=HTMLResponse)
async def report_print_view(request: Request, report_id: int):
    """Printable / save-as-PDF version — renders the email HTML in a full page.
    Open in browser → Ctrl+P → Save as PDF for a pixel-perfect match with the email.
    """
    rec = db.get_report(report_id)
    if not rec:
        raise HTTPException(404, "Report not found")
    shift_id   = rec["shift_id"]
    shift      = db.get_shift(shift_id)
    loto       = db.get_loto(shift_id)
    email_data = (
        ReportData.model_validate_json(rec["report_json"])
        if rec.get("report_json") else None
    )
    html_body = format_email_passdown_html(
        email_data,
        loto_missing=loto.get("loto_missing_keys", ""),
        pending_pms=db.get_pending_pms(shift_id),
        loto_verified=bool(loto.get("loto_verified", 0)),
    ) if email_data else rec["content"]
    # Strip outer html/body tags so we can wrap with print styles
    inner = html_body
    for tag in ("<html>", "</html>"):
        inner = inner.replace(tag, "")
    import re as _re
    inner = _re.sub(r"<body[^>]*>", "", inner).replace("</body>", "")

    title = f"{shift['shift_type']} Passdown – {shift['date']}" if shift else "Passdown Report"
    return HTMLResponse(
        f"""<!DOCTYPE html><html lang='en'><head>
<meta charset='UTF-8'><title>{title}</title>
<style>
  * {{ box-sizing:border-box; }}
  body {{ font-family:Calibri,Arial,sans-serif;font-size:14px;color:#111;
          max-width:740px;margin:32px auto;padding:0 24px;line-height:1.6; }}
  @media print {{
    body {{ margin:0;padding:16px; }}
    .no-print {{ display:none; }}
  }}
  .print-bar {{ background:#0053e2;color:#fff;padding:12px 20px;margin:-24px -24px 24px;
                display:flex;justify-content:space-between;align-items:center; }}
  .print-bar h1 {{ margin:0;font-size:15px; }}
  .print-bar button {{ background:#fff;color:#0053e2;border:none;font-weight:700;
                       padding:6px 14px;border-radius:6px;cursor:pointer;font-size:13px; }}
</style>
</head><body>
<div class='print-bar no-print'>
  <h1>&#127944; {title}</h1>
  <button onclick='window.print()'>&#128438; Print / Save as PDF</button>
</div>
{inner}
</body></html>"""
    )


@app.get("/admin/email-preview", response_class=HTMLResponse)
async def admin_email_preview(request: Request):
    """Live preview of the passdown email format with sample data."""
    if not _is_lead_authed(request):
        return _lead_redirect("/admin")
    from report_models import RewrittenHappening, OosUnit
    sample = ReportData(
        date=datetime.now().strftime("%Y-%m-%d"),
        shift_type="S2",
        lead_name="Isaac S.",
        dc_number=DC_NUMBER,
        vendors=[],
        happenings=[
            RewrittenHappening(title="Shift Happenings",
                paragraph="Worked on scheduled PMs."),
            RewrittenHappening(title="BALER0005",
                paragraph="An ABM associate requested help to secure the bale. Only 2 wires were fastened. They may need help with the next bale as well."),
            RewrittenHappening(title="TSP0004",
                paragraph="Test-drove the equipment in different environments to reproduce the error, but it did not occur. Further troubleshooting is required."),
            RewrittenHappening(title="Training",
                paragraph="Boom lift training session for Jonah."),
        ],
        ro=[],
        rts=[],
        oos=[
            OosUnit(unit="M2785"), OosUnit(unit="WAV0015"), OosUnit(unit="TK51"),
            OosUnit(unit="RTU-S-02"), OosUnit(unit="CHAR0020"), OosUnit(unit="TSP0004"),
        ],
        pm_count=4,
    )
    body    = format_email_passdown(sample)
    escaped = body.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    from_addr = outlook_sender.get_sender_address() or ""
    reply_to  = db.get_setting("email_reply_to", "").strip() or db.get_setting("email_to", "").split(",")[0].strip()
    from_name = db.get_setting("email_from_name", "IND3 Shift Passdown").strip()
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Email Preview — IND3 Passdown</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-100 min-h-screen flex items-start justify-center py-12 px-4">
  <div class="w-full max-w-2xl">
    <div class="flex items-center justify-between mb-4">
      <h1 class="text-lg font-black text-gray-800">&#128233; Email Preview</h1>
      <a href="/admin" class="text-sm text-blue-600 hover:underline">&larr; Back to Admin</a>
    </div>
    <div class="text-xs text-gray-500 mb-2">This is exactly what recipients see when a shift closes.</div>

    <!-- Fake email client chrome -->
    <div class="bg-white rounded-2xl shadow-lg border border-gray-200 overflow-hidden">
      <div class="bg-gray-50 border-b border-gray-200 px-6 py-4">
        <div class="flex flex-col gap-1 text-sm">
          <div class="flex gap-2">
            <span class="text-gray-400 w-16 shrink-0">From:</span>
            <span class="font-semibold text-gray-800">{from_name}</span>
          </div>
          <div class="flex gap-2">
            <span class="text-gray-400 w-16 shrink-0">Reply-To:</span>
            <span class="text-gray-700">{reply_to or 'same as recipients'}</span>
          </div>
          <div class="flex gap-2">
            <span class="text-gray-400 w-16 shrink-0">To:</span>
            <span class="text-gray-700">{db.get_setting('email_to', 'mt7-60@wal-mart.com')}</span>
          </div>
          <div class="flex gap-2">
            <span class="text-gray-400 w-12 shrink-0">Subject:</span>
            <span class="font-semibold text-gray-800">S2 Passdown</span>
          </div>
        </div>
      </div>
      <div class="px-6 py-6">
        <pre class="font-sans text-sm text-gray-800 whitespace-pre-wrap leading-relaxed">{escaped}</pre>
        <div class="mt-6 pt-4 border-t border-gray-100 text-xs text-gray-400 italic">
          [Your Outlook signature appears here automatically]
        </div>
      </div>
    </div>

    <!-- Data flow explainer -->
    <div class="mt-8 bg-white rounded-2xl shadow border border-gray-200 p-6">
      <h2 class="font-black text-gray-800 mb-4">&#128196; How the data gets filled in</h2>
      <ol class="space-y-4 text-sm text-gray-700">
        <li class="flex gap-3">
          <span class="bg-blue-100 text-blue-700 font-black rounded-full w-6 h-6 flex items-center justify-center shrink-0 text-xs">1</span>
          <div><strong>MST logs into the tablet/laptop</strong> &mdash; they scan or type their badge ID. The system starts a shift record with their name, shift type, and date.</div>
        </li>
        <li class="flex gap-3">
          <span class="bg-blue-100 text-blue-700 font-black rounded-full w-6 h-6 flex items-center justify-center shrink-0 text-xs">2</span>
          <div><strong>Tech fills in entries during shift</strong> &mdash; unit ID, what the issue was, what they did, what the result was. Each entry is saved to the database instantly.</div>
        </li>
        <li class="flex gap-3">
          <span class="bg-blue-100 text-blue-700 font-black rounded-full w-6 h-6 flex items-center justify-center shrink-0 text-xs">3</span>
          <div><strong>Lead reviews &amp; generates report</strong> &mdash; the lead hits &ldquo;Generate Report&rdquo; which rewrites all the raw entries into clean professional paragraphs. They can edit before saving.</div>
        </li>
        <li class="flex gap-3">
          <span class="bg-blue-100 text-blue-700 font-black rounded-full w-6 h-6 flex items-center justify-center shrink-0 text-xs">4</span>
          <div><strong>MST hits &ldquo;End Shift&rdquo;</strong> &mdash; the report is finalized, the shift is closed, and this email fires automatically to everyone in the recipients list. Outlook sends it in the background.</div>
        </li>
        <li class="flex gap-3">
          <span class="bg-green-100 text-green-700 font-black rounded-full w-6 h-6 flex items-center justify-center shrink-0 text-xs">✓</span>
          <div><strong>Management gets the email</strong> &mdash; formatted exactly as shown above, with the Outlook signature appended automatically. No MST has to type or send anything manually.</div>
        </li>
      </ol>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(html)



async def admin_test_email(request: Request):
    if not _is_lead_authed(request):
        return _lead_redirect("/admin")
    to_raw  = db.get_setting("email_to", "").strip()
    to_list = [a.strip() for a in to_raw.split(",") if a.strip()]
    if not to_list:
        return RedirectResponse("/admin?err=Add+recipients+first", status_code=303)
    from_name = db.get_setting("email_from_name", "IND3 Shift Passdown").strip()
    reply_to  = db.get_setting("email_reply_to", "").strip() or (to_list[0] if to_list else "")
    try:
        await outlook_sender.send_email(
            to_list,
            "IND3 Passdown — Test Email ✅",
            "This is a test email from the IND3 Shift Passdown system.\n\nEmail is working! 🐾",
            from_name=from_name,
            reply_to=reply_to,
        )
        return RedirectResponse("/admin?msg=Test+email+sent+%E2%9C%85", status_code=303)
    except Exception as exc:
        from urllib.parse import quote
        return RedirectResponse(f"/admin?err={quote(str(exc))}", status_code=303)


@app.post("/admin/email-test")
async def admin_test_email(request: Request):
    if not _is_lead_authed(request):
        return _lead_redirect("/admin")
    to_raw  = db.get_setting("email_to", "").strip()
    to_list = [a.strip() for a in to_raw.split(",") if a.strip()]
    if not to_list:
        return RedirectResponse("/admin?err=Add+recipients+first", status_code=303)
    from_name = db.get_setting("email_from_name", "IND3 Shift Passdown").strip()
    try:
        await outlook_sender.send_email(
            to_list,
            "IND3 Passdown — Test Email ✅",
            "This is a test email from the IND3 Shift Passdown system.\n\nEmail is working!",
            from_name=from_name,
        )
        return RedirectResponse("/admin?msg=Test+email+sent+%E2%9C%85", status_code=303)
    except Exception as exc:
        from urllib.parse import quote
        return RedirectResponse(f"/admin?err={quote(str(exc))}", status_code=303)
        return RedirectResponse("/admin?msg=Test+email+sent+%E2%9C%85", status_code=303)
    except Exception as exc:
        from urllib.parse import quote
        return RedirectResponse(f"/admin?err={quote(str(exc))}", status_code=303)


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
async def lead_summary(request: Request, shift_id: int = 0):
    if not _is_lead_authed(request):
        return HTMLResponse("")
    shift = db.get_shift(shift_id) if shift_id else db.get_active_shift()
    if not shift:
        return HTMLResponse("")
    entries = db.get_entries(shift["id"])
    done    = db.get_done_techs(shift["id"])
    techs   = {e["tech_name"] for e in entries if e.get("source") != "pm"}
    downs   = sum(1 for e in entries if e["priority"] == "down")
    degs    = sum(1 for e in entries if e["priority"] == "degraded")
    pm_count = db.get_pm_count(shift["id"])
    return templates.TemplateResponse(request, "partials/summary_strip.html", {
        "total":    len(entries),
        "downs":    downs,
        "degs":     degs,
        "techs":    len(techs),
        "done":     len(done),
        "created":  shift["created_at"],
        "pm_count": pm_count,
    })


# ── Happenings preview (live partial for generate panel) ───────────────────────

@app.get("/lead/happenings-preview", response_class=HTMLResponse)
async def lead_happenings_preview(request: Request, shift_id: int):
    """Returns the live entry list shown inside the generate panel."""
    if not _is_lead_authed(request):
        return HTMLResponse("")
    entries = db.get_entries(shift_id)
    return templates.TemplateResponse(
        request, "partials/happenings_preview.html", {"entries": entries}
    )


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


@app.get("/admin/associates/{assoc_num}/edit-form", response_class=HTMLResponse)
async def admin_assoc_edit_form(request: Request, assoc_num: str):
    """Swap a roster row into an inline edit form -- fix typos, reassign
    assoc numbers, flip MST<->tech, without leaving the admin page."""
    if not _is_lead_authed(request):
        return HTMLResponse("", status_code=403)
    assoc = db.get_associate(assoc_num)
    if not assoc:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "partials/assoc_row_edit.html", {"a": assoc}
    )


@app.get("/admin/associates/{assoc_num}/edit-form/cancel", response_class=HTMLResponse)
async def admin_assoc_edit_cancel(request: Request, assoc_num: str):
    """Revert an in-progress edit row back to the normal display row."""
    if not _is_lead_authed(request):
        return HTMLResponse("", status_code=403)
    assoc = db.get_associate(assoc_num)
    if not assoc:
        raise HTTPException(status_code=404)
    return templates.TemplateResponse(
        request, "partials/assoc_row.html", {"a": assoc}
    )


@app.post("/admin/associates/{assoc_num}/edit", response_class=HTMLResponse)
async def admin_assoc_edit_save(
    request: Request,
    assoc_num: str,
    new_assoc_num: str = Form(...),
    full_name:     str = Form(...),
    shift:         str = Form(""),
    is_mst:        str = Form("0"),
):
    """Save an edited roster row -- rename, re-number, reassign shift, or
    flip MST<->tech status all in one go."""
    global _ROSTER
    if not _is_lead_authed(request):
        return HTMLResponse("", status_code=403)
    if not new_assoc_num.strip() or not full_name.strip():
        return HTMLResponse(
            '<p class="text-red-600 text-xs font-semibold p-2">\u26a0\ufe0f Associate # and Name are required.</p>'
        )
    db.update_associate(assoc_num, new_assoc_num.strip(), full_name.strip(),
                        shift.strip(), is_mst=(is_mst == "1"))
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
    carry_overs = db.get_carry_over_entries(current_shift_id=shift["id"])
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
    shift = _resolve_my_shift(request)
    rts_units = db.get_rts_events(shift["id"]) if shift else []
    html = templates.env.get_template("partials/rts_log.html").render(rts_units=rts_units)
    return HTMLResponse(html, headers={"HX-Trigger": json.dumps({"rtsUpdated": {"count": len(rts_units)}})})


@app.delete("/lead/rts-log/{unit}", response_class=HTMLResponse)
async def delete_rts_entry(request: Request, unit: str):
    """Remove a single entry from the current shift's RTS log."""
    if not _is_lead_authed(request):
        raise HTTPException(status_code=403)
    shift = _resolve_my_shift(request)
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
    shift = _resolve_my_shift(request)
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


def _v3_report_vendors(shift_id: int) -> list[str]:
    """Read V3 vendor records if the V3 tables exist; V2-only DBs stay valid."""
    with db._conn() as c:
        exists=c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='v3_vendors'").fetchone()
        if not exists:
            return []
        return [row[0] for row in c.execute(
            "SELECT name FROM v3_vendors WHERE shift_id=? ORDER BY id", (shift_id,))]


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
    _require_shift_access(request, shift_id)
    shift = db.get_shift(shift_id)
    if not shift or not shift.get("is_active"):
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

    # V3 uses exactly the same deterministic source and text rules as the
    # Command Center and report preview. Never send these remarks through the
    # legacy AI rewrite/summarizer paths, which may invent work or drift from
    # the MST's approved text.
    from v3_report_layout import build_shift_happenings
    from v3_proofread import proofread
    db_entries = db.get_entries(shift_id)
    with db._conn() as c:
        # Older pre-V3 shifts may have no sidecar metadata yet.
        try:
            meta_rows = c.execute(
                "SELECT * FROM v3_entry_meta WHERE shift_id=?", (shift_id,)
            ).fetchall()
        except Exception as exc:
            if "no such table" not in str(exc).lower():
                raise
            meta_rows = []
    meta_by_id = {m["entry_id"]: dict(m) for m in meta_rows}
    from v3_ollama_background import approved_for_report
    happenings = build_shift_happenings(db_entries, meta_by_id, approved_remarks=approved_for_report(shift_id))
    for title, note in zip(extra_title, extra_note):
        if title.strip() and note.strip():
            happenings.append(RewrittenHappening(
                title=title.strip(), paragraph=proofread(note.strip())
            ))

    # OOS always comes from the persistent DB — not the form
    oos_units = db.get_oos_units()

    data = ReportData(
        date=shift["date"],
        shift_type=shift["shift_type"],
        lead_name=shift["lead_name"],
        dc_number=DC_NUMBER,
        # V3 Command Center vendors are persisted independently of V2's form.
        # Include both sources so report generation never drops V3 entries.
        vendors=list(dict.fromkeys(
            [v.strip() for v in vendor_name if v.strip()] + _v3_report_vendors(shift_id)
        )),
        happenings=happenings,
        pm_count=db.get_pm_count(shift_id),
        ro=[r.strip() for r in ro_item if r.strip()],
        rts=rts_clean,
        oos=[OosUnit(unit=u) for u in oos_units],
        shift_notes=db.get_shift_notes(shift_id),
    )

    email_text   = format_email(data)
    report_json  = data.model_dump_json()
    report_id    = db.save_report(shift_id, email_text, report_json)
    no_happenings = not happenings

    return templates.TemplateResponse(
        request, "partials/report_output.html",
        {"report_id": report_id, "no_happenings": no_happenings},
    )


@app.get("/report/{report_id}/docx")
async def download_docx(report_id: int):
    """Serve email HTML as .doc — Word opens it natively, looks identical to the email."""
    rec = db.get_report(report_id)
    if not rec or not rec.get("report_json"):
        raise HTTPException(status_code=404, detail="Report data not found")
    data  = ReportData.model_validate_json(rec["report_json"])
    loto  = db.get_loto(rec["shift_id"])
    pms   = db.get_pending_pms(rec["shift_id"])
    html_body = format_email_passdown_html(
        data,
        loto_missing=loto.get("loto_missing_keys", ""),
        pending_pms=pms or None,
        loto_verified=bool(loto.get("loto_verified", 0)),
    )
    fname = f"passdown_{rec['generated_at'].replace(' ', '_').replace(':', '-')}.doc"
    return Response(
        content=html_body.encode("utf-8"),
        media_type="application/msword",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


# ── EML download (opens in Outlook as a pre-filled draft) ────────────────

def _build_eml(subject: str, to_addrs: str, html_body: str) -> bytes:
    """Build a standards-compliant EML file. Outlook opens it as a ready draft."""
    import base64
    encoded = base64.b64encode(html_body.encode("utf-8")).decode("ascii")
    # Wrap base64 at 76 chars per RFC 2045
    wrapped = "\r\n".join(encoded[i:i+76] for i in range(0, len(encoded), 76))
    lines = [
        "X-Unsent: 1",
        "MIME-Version: 1.0",
        f"To: {to_addrs}",
        f"Subject: {subject}",
        "Content-Type: text/html; charset=UTF-8",
        "Content-Transfer-Encoding: base64",
        "",
        wrapped,
    ]
    return _sigs.embed_spark_in_eml("\r\n".join(lines).encode("utf-8"))


@app.get("/report/{report_id}/eml")
async def download_eml(report_id: int):
    """Generate .eml from saved report — opens Outlook as a pre-filled draft."""
    rec = db.get_report(report_id)
    if not rec or not rec.get("report_json"):
        raise HTTPException(status_code=404, detail="Report not found")

    data = ReportData.model_validate_json(rec["report_json"])
    loto = db.get_loto(rec["shift_id"])
    pms  = db.get_pending_pms(rec["shift_id"])
    html_body = format_email_passdown_html(
        data,
        loto_missing=loto.get("loto_missing_keys", ""),
        pending_pms=pms or None,
        loto_verified=bool(loto.get("loto_verified", 0)),
    )
    to_addrs = db.get_setting("email_to", "").strip()
    subject  = (
        f"{data.shift_type} Passdown"
    )
    html_body = _sigs.append_signature(html_body, data.lead_name, data.shift_type)
    eml  = _build_eml(subject, to_addrs, html_body)
    fname = f"passdown_{rec['generated_at'].replace(' ', '_').replace(':', '-')}.eml"
    return Response(
        content=eml,
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/report/{report_id}/eml")
async def download_eml_edited(
    report_id: int,
    subject:   str = Form(...),
    html_body: str = Form(...),
):
    """Generate .eml from the modal’s editor content (may contain manual edits)."""
    rec = db.get_report(report_id)
    if not rec:
        raise HTTPException(status_code=404, detail="Report not found")
    to_addrs = db.get_setting("email_to", "").strip()
    if rec.get("report_json"):
        data = ReportData.model_validate_json(rec["report_json"])
        html_body = _sigs.append_signature(html_body, data.lead_name, data.shift_type)
    else:
        html_body = _sigs.append_signature(html_body, "")
    eml  = _build_eml(subject, to_addrs, html_body)
    fname = f"passdown_{rec['generated_at'].replace(' ', '_').replace(':', '-')}.eml"
    return Response(
        content=eml,
        media_type="message/rfc822",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/report/{report_id}/pdf")
async def download_pdf(report_id: int):
    rec = db.get_report(report_id)
    if not rec or not rec.get("report_json"):
        raise HTTPException(status_code=404, detail="Report data not found")
    data  = ReportData.model_validate_json(rec["report_json"])
    loto  = db.get_loto(rec["shift_id"])
    pms   = db.get_pending_pms(rec["shift_id"])
    fname = f"passdown_{rec['generated_at'].replace(' ', '_').replace(':', '-')}.pdf"
    return Response(
        content=export_pdf(data, pending_pms=pms or None, loto_missing=loto.get("loto_missing_keys","")),
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

def _refresh_v3_roster_from_db():
    """Refresh signed V3 login eligibility after admin roster edits."""
    global _ROSTER
    _ROSTER = db.associates_to_roster()


# ── V3-only MST workspace ─────────────────────────────────────────────────────
# Legacy handlers remain internal for the V3 report/export engine only.
from v3_routes import create_v3_router
_v3_router = create_v3_router(
    templates=templates,
    is_lead_authed=_is_lead_authed,
    can_manage_shift=_can_manage_shift,
    resolve_my_shift=_resolve_my_shift,
    shift_window=_shift_window,
    lead_redirect=_lead_redirect,
    get_mst_roster=lambda: _ROSTER,
    verify_mst_pin=lambda pin: bool(pin) and hashlib.sha256(pin.encode()).hexdigest() == _get_pin_hash(),
    start_eligibility=_check_shift_start_eligibility,
    allowed_shift_days=_WEEKDAY_SHIFTS,
    now_func=_indianapolis_now,
    get_v2_cookie_value=_get_pin_hash,
    legacy_lead_page=lead_page,
    generate_report=lead_generate,
    build_eml_response=_build_eml_response,
    refresh_roster=_refresh_v3_roster_from_db,
)
app.include_router(_v3_router)

# Retire V2 routes before serving; preserve only V3 and protected exports.
from v3_only import enable_v3_only
enable_v3_only(app, roster_provider=lambda: _ROSTER)

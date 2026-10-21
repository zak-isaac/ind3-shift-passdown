#!/usr/bin/env python3
"""Apply IND3 V3 login redesign to the existing repository. Does not touch V2 UI."""
from pathlib import Path
import sys

root = Path(sys.argv[1]).expanduser().resolve() if len(sys.argv)>1 else Path.cwd()
route = root/'v3_routes.py'
main = root/'main.py'
identity = root/'v3_identity.py'
template = root/'templates/v3/login.html'
for f in (route, main, identity, template):
    if not f.exists():
        sys.exit(f'Missing {f}. Run this against the IND3 project folder.')

s = route.read_text()
start = s.find('    def entry_page(request, error=""):')
end = s.find('    def ensure_tables():', start)
if start<0 or end<0: sys.exit('V3 route layout has changed. No changes made.')
if 'verify_master_code=None' not in s:
    s=s.replace('get_v2_cookie_value=None, legacy_lead_page=None):','get_v2_cookie_value=None, legacy_lead_page=None, verify_master_code=None):',1)
new = '''    def clock():
        return now_func() if now_func else datetime.now()

    def shift_date_now(code):
        """Pick the actual start date, including yesterday for overnight shifts."""
        now = clock()
        for day in (now.date(), now.date() - timedelta(days=1)):
            if allowed_shift_days and day.weekday() not in allowed_shift_days.get(code, ()):
                continue
            window = shift_window(code, day)
            if window and window[0] - timedelta(hours=1) <= now <= window[1]:
                return day
        return None

    def current_shifts():
        """Close expired shifts before rendering login; hide premature/future shifts."""
        now = clock()
        result = []
        for shift in db.get_active_shifts():
            try:
                day = date.fromisoformat(shift["date"])
                window = shift_window(shift["shift_type"], day)
                if window and now > window[1]:
                    db.close_shift(shift["id"])
                    continue
                if window and window[0] - timedelta(hours=1) <= now <= window[1]:
                    result.append(shift)
            except (ValueError, KeyError, TypeError):
                continue
        return result

    def entry_page(request, error="", master_step=False):
        now = clock()
        eligible = []
        active = current_shifts()
        for code in ("S1", "S2", "S3", "S4", "S5"):
            day = shift_date_now(code)
            if day:
                eligible.append({"code":code,"date":day.isoformat(),
                                 "active":any(s["shift_type"] == code for s in active)})
        return templates.TemplateResponse(request, "v3/login.html", {
            "error":error, "shifts":active, "today":now.date().isoformat(),
            "eligible_shifts":eligible, "master_step":master_step,
        })

    def go_to_shift(who, sid):
        response = RedirectResponse(f"/v3?shift_id={sid}", status_code=303)
        set_session_cookies(response, who, sid)
        return response

    @router.get("/login", response_class=HTMLResponse)
    async def v3_login_page(request:Request):
        who = decode_session(request)
        return entry_page(request, master_step=bool(who and who["profile"].get("is_manager") and who["shift_id"] == 0))

    @router.post("/login", response_class=HTMLResponse)
    async def v3_login_submit(request:Request, associate_id:str=Form(""),
                              shift_id:int=Form(0), mode:str=Form("start")):
        if get_mst_roster is None:
            return entry_page(request, "Associate roster is unavailable.")
        associate = associate_id.strip()
        profile = get_mst_roster().get(associate)
        if not profile or profile.get("is_manager"):
            return entry_page(request, "Associate number not found in the current roster.")
        who = {"associate":associate,"profile":profile}
        active = current_shifts()
        owned = [sh for sh in active if shift_owned_by(sh, who)]
        if mode == "resume":
            match = next((sh for sh in owned if sh["id"] == shift_id), None)
            if not match:
                return entry_page(request, "This shift was not started by your associate number.")
            return go_to_shift(who, match["id"])
        if owned:
            return go_to_shift(who, owned[0]["id"])
        assigned = str(profile.get("shift") or "").strip().upper()
        if assigned not in ("S1","S2","S3","S4","S5"):
            return entry_page(request, "Your assigned shift is missing from the roster.")
        day = shift_date_now(assigned)
        if day is None:
            return entry_page(request, f"{assigned} is outside its scheduled working window.")
        if any(sh["shift_type"] == assigned for sh in active):
            return entry_page(request, f"{assigned} is already active under another shift starter.")
        if len(active) >= 2:
            return entry_page(request, "Two shifts are already active.")
        if start_eligibility:
            allowed, is_ot, message = start_eligibility(profile, assigned, clock())
            if not allowed:
                return entry_page(request, message or "Shift start is not permitted.")
        else:
            is_ot = False
        role = "mst" if profile.get("is_mst") else "tech"
        try:
            sid = db.start_shift(day.isoformat(), assigned,
                                 profile.get("display_name") or profile.get("full_name") or "Associate",
                                 opened_by_role=role, is_ot=is_ot if role == "mst" else False,
                                 opened_by_assoc=associate)
        except ValueError:
            return entry_page(request, "Cannot start shift: active shift limit reached.")
        return go_to_shift(who, sid)

    @router.post("/master/verify", response_class=HTMLResponse)
    async def v3_master_verify(request:Request, master_code:str=Form("")):
        if not verify_master_code or not verify_master_code(master_code.strip()):
            return entry_page(request, "Invalid management code.")
        roster = get_mst_roster() if get_mst_roster else {}
        manager = next(((key, p) for key, p in roster.items() if p.get("is_manager")), None)
        if manager is None:
            return entry_page(request, "Management profile unavailable.")
        mid, profile = manager
        response = RedirectResponse("/v3/login", status_code=303)
        response.set_cookie(v3_cookie, signed_session(mid, 0), max_age=300,
                            httponly=True, samesite="strict", path="/", secure=False)
        return response

    @router.post("/master/select", response_class=HTMLResponse)
    async def v3_master_select(request:Request, shift_type:str=Form("")):
        who = decode_session(request)
        if not who or not who["profile"].get("is_manager"):
            return entry_page(request, "Verify management access first.")
        if who["shift_id"] != 0:
            return entry_page(request, "Please verify management access again.")
        if shift_type not in ("S1","S2","S3","S4","S5"):
            return entry_page(request, "Select a shift.", master_step=True)
        day = shift_date_now(shift_type)
        if day is None:
            return entry_page(request, "That shift is outside its scheduled window.", master_step=True)
        active = current_shifts()
        existing = next((sh for sh in active if sh["shift_type"] == shift_type), None)
        if existing:
            return go_to_shift(who, existing["id"])
        if len(active) >= 2:
            return entry_page(request, "Two shifts are already active.", master_step=True)
        try:
            sid = db.start_shift(day.isoformat(), shift_type, "Manager",
                                 opened_by_role="mst", is_ot=False,
                                 opened_by_assoc=who["associate"])
        except ValueError:
            return entry_page(request, "Unable to start management shift.", master_step=True)
        return go_to_shift(who, sid)

    @router.get("/logout")
    async def v3_logout():
        response = RedirectResponse("/v3/login", status_code=303)
        for key in (v3_cookie,"lead_auth","lead_shift_id","mgr_session"):
            response.delete_cookie(key,path="/")
        return response

    @router.post("/attach/{shift_id}")
    async def attach_shift(request:Request,shift_id:int):
        who = decode_session(request)
        if not who:
            return RedirectResponse("/v3/login",status_code=303)
        shift = next((sh for sh in current_shifts() if sh["id"] == shift_id), None)
        if not shift_owned_by(shift,who):
            raise HTTPException(403,"Shift is not accessible to this associate")
        return go_to_shift(who,shift_id)

    @router.post("/start")
    async def start_shift_v3(request:Request):
        raise HTTPException(403,"Start shifts through V3 login or management selection")

'''
s = s[:start]+new+s[end:]
# In case the active shift closes while workspace is open, no changes to V2.

m=main.read_text()
anchor='    legacy_lead_page=lead_page,\n))'
if anchor not in m: sys.exit('V3 integration in main.py changed. No files modified.')
m=m.replace(anchor, '    legacy_lead_page=lead_page,\n    verify_master_code=lambda entered: entered == "8972",  # TEST ONLY; replace for production\n))',1)

v=identity.read_text()
old='if not person or person.get("is_manager"): return None'
if old not in v: sys.exit('V3 identity implementation changed. No files modified.')
v=v.replace(old,'if not person: return None',1)
anchor='    opener=(shift.get("opened_by_assoc") or "").strip()'
if anchor not in v: sys.exit('V3 ownership implementation changed. No files modified.')
v=v.replace(anchor,'    if session["profile"].get("is_manager"): return True\n'+anchor,1)

html=template.read_text()
start=html.find('<main class="flex-1')
end=html.find('<footer ',start)
if start<0 or end<0: sys.exit('V2 banner markers changed. No files modified.')
body='''<main class="flex-1 max-w-7xl w-full mx-auto px-5 py-7"><div class="grid grid-cols-1 lg:grid-cols-2 gap-5">
<section class="v3c"><div class="flex items-center justify-between mb-4"><h2 class="text-wm-dark text-2xl font-black">Welcome back</h2><span class="rounded-full bg-blue-50 text-wm-blue px-3 py-1 text-xs font-black">V3</span></div>
<p class="v3help mb-4">Enter your associate number to access your assigned shift.</p>
{% if error %}<div class="v3error mb-4" role="alert">{{ error }}</div>{% endif %}
<form method="post" action="/v3/login" class="flex flex-col gap-4"><div><label class="v3label" for="associate_id">Associate number</label><input class="v3entry" id="associate_id" name="associate_id" inputmode="numeric" autocomplete="off" maxlength="30" required placeholder="Enter your associate number"></div><button class="v3btn" type="submit">Continue &rarr;</button></form>
<div class="v3line"></div><button type="button" class="v3btn" style="background:#001e60" onclick="document.getElementById('master-login').showModal()">Management Access</button>
<p class="v3help mt-3">Testing management access opens a separate shift-selection dialog after verification.</p></section>
<section class="v3c"><h2 class="text-wm-dark text-2xl font-black mb-1">Active Shifts</h2><p class="v3help mb-4">MST leads can resume their own active shift by entering their associate number.</p>
{% for sh in shifts %}<div class="border border-blue-100 bg-blue-50 rounded-xl p-4 mb-3"><div class="flex items-center justify-between mb-2"><strong class="text-wm-dark text-2xl">{{ sh.shift_type }}</strong><span class="text-green-700 bg-green-100 rounded-full px-2 py-1 text-xs font-bold">ACTIVE</span></div><p class="text-sm text-gray-600 mb-3">{{ sh.date }} &middot; Started by {{ sh.lead_name }}</p>
<form method="post" action="/v3/login" class="flex flex-col gap-2"><input type="hidden" name="mode" value="resume"><input type="hidden" name="shift_id" value="{{ sh.id }}"><label class="v3label">Associate number<input class="v3entry mt-1" name="associate_id" inputmode="numeric" autocomplete="off" required placeholder="Enter to resume"></label><button class="v3resume" type="submit">Resume {{ sh.shift_type }}</button></form></div>{% else %}<div class="rounded-xl bg-gray-50 border border-gray-200 p-6 text-center text-gray-600 text-sm">No active shifts right now.</div>{% endfor %}
<p class="v3help mt-4">Expired shifts close automatically and remain in history.</p></section></div></main>
<dialog id="master-login" style="border:0;border-radius:16px;padding:24px;max-width:420px;width:calc(100% - 32px);box-shadow:0 25px 80px #001e6055"><div class="flex items-center justify-between mb-3"><h2 class="text-wm-dark text-xl font-black">Management Access</h2><button type="button" onclick="document.getElementById('master-login').close()" aria-label="Close">&times;</button></div><p class="v3help mb-4">Enter the testing master code.</p><form method="post" action="/v3/master/verify" class="flex flex-col gap-4"><label class="v3label">Master code<input class="v3entry mt-1" name="master_code" type="password" autocomplete="off" required></label><button type="submit" class="v3btn">Verify Code</button></form></dialog>
<dialog id="master-select" style="border:0;border-radius:16px;padding:24px;max-width:420px;width:calc(100% - 32px);box-shadow:0 25px 80px #001e6055"><div class="flex items-center justify-between mb-3"><h2 class="text-wm-dark text-xl font-black">Select a Shift</h2><button type="button" onclick="document.getElementById('master-select').close()" aria-label="Close">&times;</button></div><p class="v3help mb-4">Start or resume any eligible shift.</p><form method="post" action="/v3/master/select" class="flex flex-col gap-4"><label class="v3label">Eligible shift<select class="v3entry mt-1" name="shift_type" required><option value="">Select shift</option>{% for sh in eligible_shifts %}<option value="{{ sh.code }}">{{ sh.code }} &mdash; {{ sh.date }}{% if sh.active %} (Resume){% else %} (Start){% endif %}</option>{% endfor %}</select></label><button type="submit" class="v3btn">Continue to Shift</button></form></dialog>
{% if master_step %}<script>document.getElementById('master-select').showModal()</script>{% endif %}
'''
html=html[:start]+body+html[end:]
# All preconditions validated: apply in one pass.
route.write_text(s)
main.write_text(m)
identity.write_text(v)
template.write_text(html)
print('Updated V3 login, master popup, shift reconciliation, and identity binding.')
print('V2 templates and banner are unchanged.')

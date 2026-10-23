"""V3-only public surface for the IND3 Shift Passdown server.

V2 route registrations are retired, but their internal report builder and
export functions remain importable for V3. Legacy requests cannot mutate data.
"""
from __future__ import annotations

import re

from fastapi.responses import PlainTextResponse, RedirectResponse

# These exports are called by the V3 report page. All other legacy endpoints
# (including the old WebSocket feed and developer/V2 screens) are retired.
_REPORT_PATH = re.compile(
    r"^/report/(?P<report_id>[1-9][0-9]*)/"
    r"(?P<format>compose-outlook|docx|pdf|download|eml)$"
)


def _keep_route(route):
    path = getattr(route, "path", "")
    return (
        path == "/"
        or path == "/static"
        or path == "/v3"
        or path.startswith("/v3/")
        or bool(_REPORT_PATH.fullmatch(path.replace("{report_id}", "1")))
    )


def retire_legacy_routes(app):
    """Unregister V2 endpoints so they cannot execute or appear in OpenAPI."""
    app.router.routes[:] = [route for route in app.router.routes if _keep_route(route)]
    app.openapi_schema = None


def enable_v3_only(app, *, roster_provider):
    """Install route retirement and a final request-level legacy safety gate."""
    retire_legacy_routes(app)

    @app.middleware("http")
    async def v3_only_gateway(request, call_next):
        path = request.url.path
        method = request.method.upper()
        if path == "/":
            return RedirectResponse("/v3", status_code=303)
        if path == "/static" or path.startswith("/static/"):
            return await call_next(request)
        if path == "/v3" or path.startswith("/v3/"):
            return await call_next(request)

        export = _REPORT_PATH.fullmatch(path)
        if export and method in ("GET", "POST"):
            # A report download must not be accessible by guessing its ID.
            # The V3 session is the only accepted authorization, not V2 PIN
            # cookies, legacy master bypasses, or an associate number alone.
            import db
            from v3_identity import read as read_session

            session = read_session(request, roster_provider())
            if not session:
                return PlainTextResponse("V3 sign-in required", status_code=401)
            report = db.get_report(int(export.group("report_id")))
            if not report:
                return PlainTextResponse("Report not found", status_code=404)
            shift = db.get_shift(report["shift_id"])
            if not shift:
                return PlainTextResponse("Shift not found", status_code=404)
            if not session["profile"].get("is_manager"):
                opener = (shift.get("opened_by_assoc") or "").strip()
                if opener != session["associate"] or session["shift_id"] != shift["id"]:
                    return PlainTextResponse("Report belongs to another shift owner", status_code=403)
            return await call_next(request)

        # V2 is not merely hidden: its old POST/PUT/DELETE actions cannot run.
        if method not in ("GET", "HEAD"):
            return PlainTextResponse("V2 has been retired; use V3.", status_code=410)
        return RedirectResponse("/v3/login", status_code=303)

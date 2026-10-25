"""dev_mode.py — Developer / Test Mode for pre-production QA.

Lets Isaac (or any associate) poke at the *exact* production UI/flows
without touching real shift data. Two things make this safe:

1. A completely separate sandbox SQLite file (dbs/_devtest.db) swapped in
   via db.use_sandbox_db() for the lifetime of any request carrying the
   dev-session cookie. Production data is never read or written.
2. A frozen calendar date (Aug 7, 2026 — a Friday, so both weekday groups
   of shifts are reachable for testing) combined with the REAL wall-clock
   time-of-day, so shift time-windows still behave like a live system.

Entry: /dev/login, accepts either the literal test code below OR any real
associate number from the roster (so a tech/MST can self-test with their
own number without needing the manager code).
"""
from __future__ import annotations
import hashlib
from contextvars import ContextVar
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ── Credentials ──────────────────────────────────────────────────────────────
DEV_CODE = "T8972"   # literal test-mode entry code -- distinct from the
                      # production manager/emergency code (8972).

# ── Sandbox DB ───────────────────────────────────────────────────────────────
DEV_DB_PATH = Path(__file__).parent / "dbs" / "_devtest.db"

# ── Session cookie ───────────────────────────────────────────────────────────
DEV_COOKIE = "dev_session"
DEV_TOKEN  = hashlib.sha256(b"devmode:" + DEV_CODE.encode()).hexdigest()

# ── Frozen test date ─────────────────────────────────────────────────────────
DEV_DATE = date(2026, 8, 7)


def is_dev_credential(entered: str, roster: dict) -> bool:
    """True for the literal test code, or any known associate number.
    Deliberately permissive on WHO can get into test mode -- it's a
    read/write sandbox, not a security boundary like the real shift data.
    """
    entered = entered.strip()
    if not entered:
        return False
    if entered == DEV_CODE:
        return True
    return entered in roster


def dev_now(clock: str = "") -> datetime:
    """Use a sandbox-only simulated clock without changing production time."""
    if clock:
        try:
            return datetime.strptime(clock, "%Y-%m-%dT%H:%M")
        except (TypeError, ValueError):
            pass
    return datetime.combine(DEV_DATE, datetime.now().time())


# ── Per-request "now" override ──────────────────────────────────────────────
# Set for the lifetime of a dev-mode request (see main.py middleware) so all
# business logic (shift eligibility, date validation, home page rendering)
# transparently sees the frozen test date without threading a flag through
# every function signature. Falls back to the real clock for every normal
# production request -- the ContextVar default is None.
_NOW_OVERRIDE: ContextVar[Optional[datetime]] = ContextVar("_NOW_OVERRIDE", default=None)


def get_now() -> datetime:
    return _NOW_OVERRIDE.get() or datetime.now()


def get_today() -> date:
    return get_now().date()


def is_active() -> bool:
    return _NOW_OVERRIDE.get() is not None


class freeze_time:
    """Context manager -- pin get_now()/get_today() to `dt` for its scope."""
    def __init__(self, dt: datetime):
        self._dt = dt
        self._token = None

    def __enter__(self):
        self._token = _NOW_OVERRIDE.set(self._dt)
        return self

    def __exit__(self, *exc):
        _NOW_OVERRIDE.reset(self._token)
        return False

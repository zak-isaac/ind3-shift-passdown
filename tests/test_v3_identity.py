"""Regression test: v3_identity.read() must accept a signed manager/master
session (e.g. the shared "8972" profile, which main.py's 2759/8972
_MASTER_CODES already gate at login time) without requiring any additional
"elevated" flag or environment variable.

This guards against the 2026-10-23 regression where porting a GitHub
master-session gate into v3_identity.py silently locked every manager/master
login out, because nothing in this app's actual login flow ever sets
elevated=True and the required env vars were never configured. See the
NOTE in v3_identity.py's module docstring for the full story.
"""
import v3_identity


class _FakeRequest:
    def __init__(self, cookie_value):
        self.cookies = {v3_identity.COOKIE: cookie_value}


def test_manager_profile_session_is_accepted():
    roster = {"8972": {"is_manager": True, "display_name": "Manager"}}
    cookie = v3_identity.sign("8972", 0)
    session = v3_identity.read(_FakeRequest(cookie), roster)
    assert session is not None
    assert session["profile"]["is_manager"] is True
    assert session["associate"] == "8972"


def test_ordinary_associate_session_is_accepted():
    roster = {"1234": {"is_manager": False, "display_name": "Tech"}}
    cookie = v3_identity.sign("1234", 7)
    session = v3_identity.read(_FakeRequest(cookie), roster)
    assert session is not None
    assert session["profile"]["is_manager"] is False
    assert session["shift_id"] == 7


def test_unknown_associate_is_rejected():
    cookie = v3_identity.sign("9999", 0)
    assert v3_identity.read(_FakeRequest(cookie), {}) is None


def test_tampered_cookie_is_rejected():
    cookie = v3_identity.sign("8972", 0) + "x"
    roster = {"8972": {"is_manager": True}}
    assert v3_identity.read(_FakeRequest(cookie), roster) is None

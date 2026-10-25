"""Facility-local overnight shift windows and session continuity."""
from datetime import datetime, timedelta
from types import SimpleNamespace
import base64

import main
import v3_identity as identity
import v3_split


def test_s3_thursday_shift_stays_thursday_through_friday_morning():
    start,end=main._shift_window("S3",datetime(2026,10,22).date())
    assert start==datetime(2026,10,22,20,30)
    assert end==datetime(2026,10,23,8,0)
    assert start<=datetime(2026,10,23,6,55)<end
    assert ("S3","2026-10-22") in v3_split._windows(datetime(2026,10,23,6,55))


def test_s5_sunday_shift_stays_sunday_through_monday_morning():
    start,end=main._shift_window("S5",datetime(2026,10,25).date())
    assert start==datetime(2026,10,25,17,30)
    assert end==datetime(2026,10,26,7,0)
    assert start<=datetime(2026,10,26,5,45)<end
    assert ("S5","2026-10-25") in v3_split._windows(datetime(2026,10,26,5,45))
    # The 1-hour period beyond 06:00 is for handover/report completion.
    # It is NOT a blanket one-hour extra window for crediting WO work.
    assert ("S5","2026-10-25") not in v3_split._windows(datetime(2026,10,26,6,45))


def test_v3_mst_cookie_survives_full_s5_shift(monkeypatch):
    start=100000000.0
    monkeypatch.setattr(identity.time,"time",lambda: start)
    signed=identity.sign("1001",7)
    req=SimpleNamespace(cookies={identity.COOKIE:signed})
    roster={"1001":{"display_name":"MST Test","shift":"S5","is_mst":True}}
    monkeypatch.setattr(identity.time,"time",lambda: start+14.5*3600)
    who=identity.read(req,roster)
    assert who is not None and who["shift_id"]==7
    monkeypatch.setattr(identity.time,"time",lambda: start+18*3600+1)
    assert identity.read(req,roster) is None


def test_indianapolis_facility_clock_independent_of_host_timezone():
    # The current operational 'now' should come from Indianapolis, not host.
    now=main._indianapolis_now()
    assert now.tzinfo is None
    assert abs((datetime.now(main.ZoneInfo("America/Indiana/Indianapolis")).replace(tzinfo=None)-now).total_seconds())<5

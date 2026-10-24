"""Regression tests for v3_split.py's S1-S5 technician-evidence attribution
engine, covering the exact real-world scenarios called out in the 2026-10-24
Cetaris shift-attribution requirements:

- Thursday S3 continuing into Friday morning must not be reassigned to S4
  just because the calendar date changed (Ray Stamps).
- A Friday S4 WO administratively closed on Saturday must stay S4, not
  transfer to S5 (Isaac Ssaku).
- A clear Friday-evening S5 work-start must land in S5 (Layne Jonah).
- Weekday S1/S2 and S2/S3 overlaps resolve the same way via roster/evidence.
"""
from datetime import date
import v3_split


def _row(wo, tech, asset, start_at, in_service_at="", closed_at="", status="Closed"):
    return {"wo_number": wo, "tech_name": tech, "asset_id": asset, "status": status,
            "start_at": start_at, "in_service_at": in_service_at, "closed_at": closed_at,
            "updated_at": "", "opened_at": ""}


def test_ray_stamps_thursday_s3_continues_into_friday_morning():
    """WO 28025279 (VOIDFILL1201) and WO 28025085 (BALER0001): Ray Stamps
    started both in the small hours of Friday morning but both belong to
    Thursday's S3, per his roster home shift -- not Friday's S4, even though
    the calendar date reads Friday.
    """
    rows = [
        _row("28025279", "Ray Stamps", "VOIDFILL1201", "2026-10-23T05:55:00",
             in_service_at="2026-10-23T06:53:00"),
        _row("28025085", "Ray Stamps", "BALER0001", "2026-10-23T05:18:00"),
    ]
    roster_shifts = {"Ray Stamps": "S3"}
    accepted, pending, owned_elsewhere = v3_split.assign(
        rows, {"Ray Stamps"}, "S3", date(2026, 10, 22), roster_shifts=roster_shifts)
    assert {r["wo_number"] for r in accepted} == {"28025279", "28025085"}
    assert pending == []

    # The same two WOs must NOT also be creditable to a Friday S4 import.
    accepted_s4, pending_s4, owned_elsewhere_s4 = v3_split.assign(
        rows, {"Ray Stamps"}, "S4", date(2026, 10, 23), roster_shifts=roster_shifts)
    assert accepted_s4 == []
    assert owned_elsewhere_s4 == 2, "both WOs confidently belong to Thursday S3, not this Friday S4 import"


def test_isaac_ssaku_friday_s4_survives_saturday_administrative_close():
    """WO 28026013 (BAG0013): work started and equipment returned to service
    Friday evening within S4's window; the WO wasn't administratively closed
    until Saturday morning. The late close must never transfer ownership to S5.
    """
    rows = [_row("28026013", "Ssaku, Isaac", "BAG0013", "2026-10-23T17:17:00",
                 in_service_at="2026-10-23T17:43:00", closed_at="2026-10-24T06:15:00")]
    accepted, pending, _ = v3_split.assign(rows, {"Ssaku, Isaac"}, "S4", date(2026, 10, 23))
    assert [r["wo_number"] for r in accepted] == ["28026013"]
    assert pending == []

    accepted_s5, _, _ = v3_split.assign(rows, {"Ssaku, Isaac"}, "S5", date(2026, 10, 23))
    assert accepted_s5 == [], "a late administrative close must never move credit to S5"


def test_layne_jonah_friday_evening_lands_in_s5():
    """WO 28026131 (VOIDFILL0501): a clear Friday-evening work-start, well
    past S4's 18:00 end, unambiguously belongs to S5."""
    rows = [_row("28026131", "Layne Jonah", "VOIDFILL0501", "2026-10-23T21:50:00")]
    accepted, pending, _ = v3_split.assign(rows, {"Layne Jonah"}, "S5", date(2026, 10, 23))
    assert [r["wo_number"] for r in accepted] == ["28026131"]
    assert pending == []


def test_s1_s2_overlap_resolved_by_roster():
    """A 1pm Tuesday work-start sits in the real S1(5:30-16:00)/S2(12:00-22:30)
    overlap. Without roster or continuity evidence it's ambiguous; a roster
    home shift of S2 resolves it to S2, not S1."""
    rows = [_row("50001", "Priya Lin", "CONV001", "2026-10-20T13:00:00")]
    # No roster: genuinely ambiguous, surfaced for MST review.
    accepted, pending, _ = v3_split.assign(rows, {"Priya Lin"}, "S2", date(2026, 10, 20))
    assert accepted == []
    assert len(pending) == 1 and pending[0]["wo_number"] == "50001"

    # Roster home shift resolves the same overlap cleanly.
    accepted2, pending2, _ = v3_split.assign(
        rows, {"Priya Lin"}, "S2", date(2026, 10, 20), roster_shifts={"Priya Lin": "S2"})
    assert [r["wo_number"] for r in accepted2] == ["50001"]
    assert pending2 == []


def test_missing_timestamp_is_never_silently_dropped():
    """A selected, completed WO with no usable timing evidence at all must
    surface for MST review -- never silently vanish from either shift."""
    rows = [_row("60001", "Dana Kim", "PUMP009", "")]
    accepted, pending, _ = v3_split.assign(rows, {"Dana Kim"}, "S2", date(2026, 10, 20))
    assert accepted == []
    assert len(pending) == 1 and pending[0]["wo_number"] == "60001"


def test_standing_ownership_decision_is_honored_on_next_import():
    """Once an MST assigns a pending WO's ownership via a `decisions`
    override, the next import (by any shift) must honor it directly."""
    rows = [_row("50002", "Priya Lin", "CONV002", "2026-10-20T13:00:00")]
    accepted, pending, _ = v3_split.assign(
        rows, {"Priya Lin"}, "S2", date(2026, 10, 20), decisions={"50002": "S2"})
    assert [r["wo_number"] for r in accepted] == ["50002"]
    assert pending == []


def test_cross_day_export_excludes_already_recorded_shift_without_flagging():
    """Real-world incident (2026-10-24): an MST re-uploaded a Cetaris export
    that still mostly contained the PREVIOUS day's closed WOs (already
    correctly recorded against yesterday's S4). These must be silently
    excluded from today's S4 import -- never double-credited, never raised
    as an ambiguous/pending review item (there's no ambiguity, they're just
    not today's) -- but their count must still be reported via
    owned_elsewhere so an MST never sees an unexplained '0 new entries'.
    """
    rows = [
        _row("90001", "Carr, Alexander", "WAV0011", "2026-10-23T07:39:00"),  # yesterday's S4
        _row("90002", "Carr, Alexander", "M2360", "2026-10-24T09:00:00"),  # today's S4
    ]
    accepted, pending, owned_elsewhere = v3_split.assign(
        rows, {"Carr, Alexander"}, "S4", date(2026, 10, 24))
    assert [r["wo_number"] for r in accepted] == ["90002"]
    assert pending == []
    assert owned_elsewhere == 1

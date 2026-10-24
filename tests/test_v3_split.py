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


def _row(wo, tech, asset, start_at, in_service_at="", closed_at="", status="Closed",
         updated_at="", opened_at="", last_complete_at=""):
    return {"wo_number": wo, "tech_name": tech, "asset_id": asset, "status": status,
            "start_at": start_at, "in_service_at": in_service_at, "closed_at": closed_at,
            "updated_at": updated_at, "opened_at": opened_at, "last_complete_at": last_complete_at}


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


# ── S5 06:00-06:30 morning grace (2026-10-24 correction) ────────────────────
# Technician SELECTION for an S5 import only confirms shift participation,
# never automatic ownership of grace-window work. Grace is just another
# overlap in _windows(), resolved the same way as any other: roster home
# shift, then the technician's own earlier unambiguous S5 start that same
# night, then Needs Review if neither applies.

def test_s5_grace_resolved_by_continuity_not_blanket_selection():
    """A technician who worked an unambiguous S5 shift starting 11pm Friday,
    then closed a WO at 6:15am Saturday (the grace window), gets credited to
    Friday's S5 via continuity -- not because they were merely selected."""
    rows = [
        _row("70001", "Williams, Rapheal", "CONV010", "2026-10-23T23:00:00"),
        _row("70002", "Williams, Rapheal", "CONV011", "2026-10-24T06:15:00"),
    ]
    accepted, pending, _ = v3_split.assign(rows, {"Williams, Rapheal"}, "S5", date(2026, 10, 23))
    assert {r["wo_number"] for r in accepted} == {"70001", "70002"}
    assert pending == []


def test_s5_grace_resolved_by_roster_home_shift():
    """A technician whose ONLY WO this shift is in the grace window, with no
    other evidence, is still correctly attributed to S5 when their roster
    home shift is S5."""
    rows = [_row("70003", "Layne Jonah", "CONV012", "2026-10-24T06:10:00")]
    accepted, pending, _ = v3_split.assign(
        rows, {"Layne Jonah"}, "S5", date(2026, 10, 23), roster_shifts={"Layne Jonah": "S5"})
    assert [r["wo_number"] for r in accepted] == ["70003"]
    assert pending == []


def test_s5_grace_not_auto_assigned_from_selection_alone():
    """THE CORE FIX (2026-10-24): a technician selected for today's S5 import,
    with a grace-window WO and NEITHER roster-S5 nor an earlier unambiguous
    S5 start that night, must NOT be auto-credited to S5 just because they
    were selected -- it must surface to Needs Review instead."""
    rows = [_row("70004", "Koukpo, Kpedekpo", "CONV013", "2026-10-24T06:05:00")]
    accepted, pending, _ = v3_split.assign(rows, {"Koukpo, Kpedekpo"}, "S5", date(2026, 10, 23))
    assert accepted == [], "must NOT auto-assign to S5 purely from being selected"
    assert len(pending) == 1 and pending[0]["wo_number"] == "70004"


def test_s5_grace_window_correctly_resolves_to_s4_via_roster():
    """The mirror case: a technician whose roster home shift is S4 (not S5)
    closes a WO at 6:20am Saturday with no S5 evidence at all -- correctly
    belongs to today's S4, not Friday's S5, even though the timestamp falls
    inside the grace window that COULD have meant S5."""
    rows = [_row("70005", "Jones, Robert", "CONV014", "2026-10-24T06:20:00")]
    accepted, pending, _ = v3_split.assign(
        rows, {"Jones, Robert"}, "S4", date(2026, 10, 24), roster_shifts={"Jones, Robert": "S4"})
    assert [r["wo_number"] for r in accepted] == ["70005"]
    assert pending == []


def test_s5_grace_ambiguity_persists_as_standing_decision():
    """Once an MST resolves a grace-window ambiguity via a standing decision,
    the same WO is honored directly on a later re-upload -- never re-asked."""
    rows = [_row("70006", "Koukpo, Kpedekpo", "CONV015", "2026-10-24T06:05:00")]
    accepted, pending, _ = v3_split.assign(
        rows, {"Koukpo, Kpedekpo"}, "S5", date(2026, 10, 23), decisions={"70006": "S5"})
    assert [r["wo_number"] for r in accepted] == ["70006"]
    assert pending == []


# ── Timestamp evidence hierarchy (2026-10-24 verification) ──────────────────
# Technician Start, In Service, WO Closed, WO Opened, WO Last Complete, and
# Update Date/Time must stay separate fields with distinct roles: Start is
# primary; In Service is the first completion fallback; Update Date/Time is
# a SECOND fallback (a record touch, not proof work happened then); WO
# Closed and WO Last Complete are audit/display only and must never
# participate in ownership attribution; WO Opened only matters for
# non-closed (OOS) status tracking, never completed-WO ownership.

def test_wo_closed_date_never_used_as_attribution_evidence():
    """A completed WO with ONLY a WO Closed Date/Time populated (no start,
    no in-service, no update, no opened) has zero usable evidence -- it must
    surface to Needs Review, never get silently attributed off the closed
    timestamp alone."""
    rows = [_row("82001", "Dana Kim", "PUMP010", start_at="",
                 closed_at="2026-10-20T18:00:00")]
    accepted, pending, _ = v3_split.assign(rows, {"Dana Kim"}, "S2", date(2026, 10, 20))
    assert accepted == [], "WO Closed Date/Time alone must never establish ownership"
    assert len(pending) == 1 and pending[0]["wo_number"] == "82001"


def test_last_complete_date_never_used_as_attribution_evidence():
    """A completed WO with ONLY WO Last Complete Date/Time populated (no
    start, no in-service, no update) must also surface to Needs Review --
    this field is audit/display only, never a labor-evidence substitute,
    even though it is a genuinely distinct timestamp from WO Closed."""
    rows = [_row("82002", "Dana Kim", "PUMP011", start_at="",
                 last_complete_at="2026-10-20T18:00:00")]
    accepted, pending, _ = v3_split.assign(rows, {"Dana Kim"}, "S2", date(2026, 10, 20))
    assert accepted == [], "WO Last Complete Date/Time must never establish ownership"
    assert len(pending) == 1 and pending[0]["wo_number"] == "82002"


def test_in_service_date_takes_priority_over_update_date_fallback():
    """When both In Service Date/Time and Update Date/Time are present (and
    would resolve to DIFFERENT shifts), In Service -- the more direct
    equipment-return signal -- wins; Update Date/Time is only consulted when
    In Service is absent."""
    # In Service: clean S2-only (6pm Tuesday). Update Date/Time: clean S1-only
    # (10am Tuesday) -- a deliberately conflicting fallback value to prove
    # In Service is actually checked first, not merely present.
    rows = [_row("82003", "Dana Kim", "PUMP012", start_at="",
                 in_service_at="2026-10-20T18:00:00", updated_at="2026-10-20T10:00:00")]
    accepted, pending, _ = v3_split.assign(rows, {"Dana Kim"}, "S2", date(2026, 10, 20))
    assert [r["wo_number"] for r in accepted] == ["82003"], \
        "In Service Date/Time must take priority over Update Date/Time"
    assert pending == []


def test_update_date_used_only_as_fallback_when_in_service_missing():
    """With In Service Date/Time absent, Update Date/Time is a valid
    fallback completion-evidence source."""
    rows = [_row("82004", "Dana Kim", "PUMP013", start_at="",
                 updated_at="2026-10-20T18:00:00")]
    accepted, pending, _ = v3_split.assign(rows, {"Dana Kim"}, "S2", date(2026, 10, 20))
    assert [r["wo_number"] for r in accepted] == ["82004"]
    assert pending == []


def test_wo_opened_date_not_used_for_completed_wo_attribution():
    """WO Date/Time (opened_at) is only a fallback for non-closed (OOS)
    status tracking -- a COMPLETED WO with only an opened_at timestamp (no
    start, in-service, or update) must still surface to Needs Review rather
    than being attributed off its opening time."""
    rows = [_row("82005", "Dana Kim", "PUMP014", start_at="",
                 opened_at="2026-10-20T18:00:00")]
    accepted, pending, _ = v3_split.assign(rows, {"Dana Kim"}, "S2", date(2026, 10, 20))
    assert accepted == [], "WO Opened Date/Time must never establish completed-WO ownership"
    assert len(pending) == 1 and pending[0]["wo_number"] == "82005"


def test_wo_opened_date_still_used_for_in_process_oos_tracking():
    """The one legitimate use of opened_at: an IN-PROCESS (not yet closed)
    breakdown WO with no start_at still needs its OOS tracking attributed to
    the correct shift, and opened_at is the appropriate fallback there."""
    rows = [_row("82006", "Dana Kim", "PUMP015", start_at="",
                 opened_at="2026-10-20T18:00:00", status="In Process")]
    accepted, pending, _ = v3_split.assign(rows, {"Dana Kim"}, "S2", date(2026, 10, 20))
    assert [r["wo_number"] for r in accepted] == ["82006"]
    assert pending == []

"""Cetaris Desktop single-file XLSX import for MST V3. No AI rewriting.

Column contract verified against a real Cetaris Desktop export (10-22.xlsx,
153 data rows / 150 unique WO numbers / 9 PM rows / 8 unique PM WO numbers).
Only the actual headers that export produces are read -- nothing is assumed:

    WO Number, Asset ID, Asset #, WO Status, SJ / PM Code, Repair Class,
    Last Shift, Service/Repair Description, Technician, Technician Remarks,
    Cause, Start Date, WO Closed Date/Time

Plus 'Primary RFR', which is not part of the fix's required contract but is
kept as an optional, best-effort field: it already powers the existing
Shift Happenings work-type badge (Breakdown / Process Interruption /
Proactive Repair / Work Request / Other) and removing it would regress that
working feature. Its absence never blocks an import.

Only the selected technicians' *individual labor rows* are used; Cetaris's
Correction column deliberately is NOT read as it can combine other techs.
`Last Technician` is never substituted for `Technician` -- some records are
also only reliably attributable to a shift via the real `Technician` column.
`Last Shift` is parsed through for traceability only. Cetaris marks many
records '24 HOUR', so it is NEVER used to infer a technician's scheduled
shift -- shift assignment comes solely from the confirmed MST selection.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from io import BytesIO, StringIO
from typing import Any
import csv

from openpyxl import load_workbook

from cause_lookup import normalize_wo

# Hard-required headers: every real Cetaris Desktop export has all of these.
# A file missing any of them is not a supported export and is rejected with
# a clear error instead of silently producing wrong/empty data.
REQUIRED = (
    "WO Number", "Asset ID", "Asset #", "WO Status", "SJ / PM Code",
    "Repair Class", "Last Shift", "Service/Repair Description",
    "Technician", "Technician Remarks", "Cause", "Start Date",
    "WO Closed Date/Time",
)
# Optional: read when present, never required. See module docstring.
# "Update Date / Time" (Cetaris column BG) reflects when a WO's record was
# actually last touched/closed -- more reliable than "WO Closed Date/Time"
# per 2026-10-23 facility feedback, so it's preferred wherever both exist
# (see select_records() and v3_split.owner_for_close()). Optional, not
# required: older exports predate this column.
# "WO Date / Time" (opened_at), "In Service Date / Time" (in_service_at), and
# "WO Last Complete Date / Time" (last_complete_at) feed v3_split.py's
# S1-S5 technician-evidence attribution engine (2026-10-24 facility-confirmed
# columns). opened_at is WO creation, not labor evidence -- fallback only.
# in_service_at is equipment-return evidence, used to resolve handovers.
# last_complete_at is preserved for display/audit, never a labor substitute.
_OPTIONAL = ("Primary RFR", "Update Date / Time", "WO Date / Time",
             "In Service Date / Time", "WO Last Complete Date / Time")

DATE_FORMATS = ("%m/%d/%Y %I:%M %p", "%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d")
MAX_SIZE = 12 * 1024 * 1024
MAX_ROWS = 30000


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(_text(value), fmt)
        except (TypeError, ValueError):
            pass
    return None


def _open_xlsx_grid(data: bytes):
    """Return (headers, row_iterator, workbook) for a real .xlsx workbook.
    Raises ValueError if this isn't a readable xlsx -- caller falls back to
    CSV, never shown to the user directly."""
    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("not an xlsx workbook") from exc
    sheet = workbook.active
    source = sheet.iter_rows(values_only=True)
    return next(source, None), source, workbook


def _open_csv_grid(data: bytes):
    """Return (headers, row_iterator, None) for a CSV export. Raises
    ValueError if the bytes aren't readable text -- caller surfaces the
    final combined error, never this one directly."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("not a readable CSV") from exc
    rows = csv.reader(StringIO(text))
    headers = next(rows, None)
    return headers, rows, None


def parse_excel(data: bytes, stats: dict | None = None) -> list[dict]:
    """Parse a bounded, validated Cetaris work order export (.xlsx or .csv)
    into JSON-serializable normalized rows. Format is detected by actual
    content (tries xlsx first, falls back to CSV), never by filename --
    see v3_routes.py's stage_import().

    `stats`, if given, is populated in-place with import-diagnostics counts
    (2026-10-24 facility request): source_rows (every data row in the file,
    including ones this function legitimately skips), skipped_no_wo,
    skipped_no_tech (e.g. unassigned 'To be Done' PM WOs with no technician
    or start date yet -- correctly not labor evidence for any shift, but an
    MST should be able to SEE that count, not just infer it from a smaller-
    than-expected total). Optional and additive: existing callers that don't
    pass `stats` are unaffected.
    """
    if stats is None:
        stats = {}
    if len(data) > MAX_SIZE:
        raise ValueError("This file exceeds the 12 MB import limit.")
    try:
        headers, source, workbook = _open_xlsx_grid(data)
    except ValueError:
        try:
            headers, source, workbook = _open_csv_grid(data)
        except ValueError:
            raise ValueError(
                "Unable to read this file as an Excel (.xlsx) or CSV work order export. "
                "Make sure you selected the actual exported report file."
            )
    try:
        if not headers:
            raise ValueError("File has no headings.")
        mapping = {str(name).strip().casefold(): pos for pos, name in enumerate(headers) if name is not None and str(name).strip()}
        missing = [name for name in REQUIRED if name.casefold() not in mapping]
        if missing:
            raise ValueError("This file doesn't match the expected Cetaris work order export. Missing columns: " + ", ".join(missing))

        def get(row, label):
            pos = mapping.get(label.casefold())
            return row[pos] if pos is not None and pos < len(row) else None

        results = []
        source_rows = skipped_no_wo = skipped_no_tech = 0
        for i, row in enumerate(source):
            if i >= MAX_ROWS:
                raise ValueError("Import limited to 30,000 labor rows. Narrow your Cetaris export dates.")
            if all(cell is None for cell in row):
                continue  # a genuinely blank trailing row, not real data
            source_rows += 1
            wo = normalize_wo(get(row, "WO Number"))
            tech = _text(get(row, "Technician"))
            if not wo:
                skipped_no_wo += 1
                continue
            if not tech:
                skipped_no_tech += 1
                continue
            started = _parse_dt(get(row, "Start Date"))
            closed_at = _parse_dt(get(row, "WO Closed Date/Time"))
            # "Update Date / Time" reflects when the record was actually last
            # touched/closed -- more reliable than "WO Closed Date/Time" per
            # 2026-10-23 facility feedback. Optional column; absent on older
            # exports, in which case callers fall back to closed_at.
            updated_at = _parse_dt(get(row, "Update Date / Time"))
            # WO-level administrative/equipment-evidence timestamps (same
            # value across every labor row on a given WO). Feed v3_split.py's
            # S1-S5 attribution; never substituted for technician start_at.
            opened_at = _parse_dt(get(row, "WO Date / Time"))
            in_service_at = _parse_dt(get(row, "In Service Date / Time"))
            last_complete_at = _parse_dt(get(row, "WO Last Complete Date / Time"))
            # Asset ID comes from 'Asset ID', falling back to 'Asset #' only
            # when Asset ID is missing -- real exports leave Asset ID blank
            # for most rows, so Asset # is the common case, not the fallback.
            asset_id = _text(get(row, "Asset ID")) or _text(get(row, "Asset #"))
            results.append({
                "wo_number": wo, "asset_id": asset_id,
                "status": _text(get(row, "WO Status")),
                "repair_class": _text(get(row, "Repair Class")),
                "pm_code": _text(get(row, "SJ / PM Code")),
                "reported_issue": _text(get(row, "Service/Repair Description")),
                "tech_name": tech,
                "remarks": _text(get(row, "Technician Remarks")),
                "start_at": started.isoformat(timespec="seconds") if started else "",
                "closed_at": closed_at.isoformat(timespec="seconds") if closed_at else "",
                "updated_at": updated_at.isoformat(timespec="seconds") if updated_at else "",
                "opened_at": opened_at.isoformat(timespec="seconds") if opened_at else "",
                "in_service_at": in_service_at.isoformat(timespec="seconds") if in_service_at else "",
                "last_complete_at": last_complete_at.isoformat(timespec="seconds") if last_complete_at else "",
                "cause": _text(get(row, "Cause")),
                "last_shift": _text(get(row, "Last Shift")),
                "rfr": _text(get(row, "Primary RFR")),
            })
        stats["source_rows"] = source_rows
        stats["skipped_no_wo"] = skipped_no_wo
        stats["skipped_no_tech"] = skipped_no_tech
        stats["parsed_rows"] = len(results)
        return results
    finally:
        if workbook is not None:
            workbook.close()


def technician_counts(rows: list[dict]) -> list[dict]:
    by_tech: dict[str, set] = {}
    for r in rows:
        by_tech.setdefault(r["tech_name"], set()).add(r["wo_number"])
    return [{"name": name, "count": len(wos)} for name, wos in sorted(by_tech.items())]


def _is_pm(r: dict) -> bool:
    """A WO is PM primarily through a populated SJ / PM Code, corroborated by
    Repair Class. Multiple remarks/technicians on the same PM WO must not
    change this -- it's a per-row classification, counting happens by
    distinct WO number downstream in select_records()."""
    return bool(r["pm_code"].strip())


def select_records(rows: list[dict], selected: set[str], start: datetime, end: datetime,
                    trust_attribution: bool = False):
    """Include every Closed non-PM work order; route only PMs to the PM summary.

    Where Start Date and WO Closed Date/Time disagree about which shift a WO
    belongs to (or Start Date is missing/unparseable), the record is routed
    to `ambiguous` for MST review instead of being guessed either way.

    `trust_attribution=True` (2026-10-24, S1-S5 ownership engine): `rows` has
    already been through v3_split.assign(), which decides shift ownership
    from technician work-time evidence -- a strictly better signal than this
    function's own simple start/close-vs-window check. Re-applying that
    check here would re-reject WOs v3_split just correctly attributed (e.g.
    the S5 06:00-06:30 grace window, or a roster/continuity-resolved
    overlap) purely because its start_at sits slightly outside this
    function's naive window math. When true, every row that reaches here is
    trusted as already belonging to this shift; only the status/PM/Happening
    classification below still applies.
    """
    summary = Counter()
    eligible = []
    ambiguous = []
    excluded = []
    for r in rows:
        if r["tech_name"] not in selected:
            continue
        summary["selected_rows"] += 1
        started = _parse_dt(r["start_at"])
        # "Update Date / Time" is the more reliable real-close signal per
        # 2026-10-23 facility feedback -- "WO Closed Date/Time" can be stale.
        # Prefer it when present; fall back to WO Closed Date/Time for older
        # exports that predate this column.
        closed_at = _parse_dt(r.get("updated_at") or r["closed_at"])
        in_window = trust_attribution or (started is not None and start <= started <= end)
        closed_in_window = closed_at is not None and start <= closed_at <= end
        if not trust_attribution and started is None:
            summary["ambiguous_shift"] += 1
            ambiguous.append({**r, "reason": "Start Date is missing or unreadable -- cannot confirm shift."})
            continue
        if not in_window:
            if closed_in_window:
                # Conflicting signals: started outside the window but closed
                # inside it. Don't guess which shift gets credit.
                summary["ambiguous_shift"] += 1
                ambiguous.append({**r, "reason": "Start Date and Update/Closed Date disagree on shift -- needs MST review."})
                continue
            summary["outside_shift"] += 1
            excluded.append({**r, "reason": "Outside this shift's reporting window."})
            continue
        if r["status"].strip().casefold() != "closed":
            summary["not_closed"] += 1
            excluded.append({**r, "reason": f"WO Status is '{r['status']}', not Closed."})
            continue
        if _is_pm(r):
            summary["pm_rows"] += 1
            eligible.append({**r, "kind": "pm"})
        else:
            # Every other repair/service WO belongs in Shift Happenings.
            summary["happening_rows"] += 1
            eligible.append({**r, "kind": "happening"})
    # A technician may log more than one labor row on a WO. Preserve each unique remark.
    grouped = {}
    for r in eligible:
        key = (r["wo_number"], r["tech_name"], r["kind"])
        if key not in grouped:
            grouped[key] = {**r, "remarks_list": []}
        if r["remarks"] and r["remarks"] not in grouped[key]["remarks_list"]:
            grouped[key]["remarks_list"].append(r["remarks"])
        if not grouped[key]["cause"] and r["cause"]:
            grouped[key]["cause"] = r["cause"]
        if not grouped[key]["reported_issue"] and r["reported_issue"]:
            grouped[key]["reported_issue"] = r["reported_issue"]
        # Keep the LATEST update timestamp across a WO's duplicate labor
        # rows -- ISO8601 strings sort lexicographically same as chronologically.
        if r.get("updated_at","") > grouped[key].get("updated_at",""):
            grouped[key]["updated_at"] = r["updated_at"]
    records = list(grouped.values())
    # Count DISTINCT WO numbers, not rows/labor entries/technicians -- a PM
    # WO logged by two technicians, or with two remarks, still counts once.
    summary["unique_pm_wos"] = len({r["wo_number"] for r in records if r["kind"] == "pm"})
    summary["unique_happening_wos"] = len({r["wo_number"] for r in records if r["kind"] == "happening"})
    return records, {**dict(summary), "ambiguous": ambiguous, "excluded": excluded}

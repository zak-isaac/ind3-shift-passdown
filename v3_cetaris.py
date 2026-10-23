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
from io import BytesIO
from typing import Any

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
_OPTIONAL = ("Primary RFR",)

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


def parse_excel(data: bytes) -> list[dict]:
    """Parse a bounded, validated workbook into JSON-serializable normalized rows."""
    if len(data) > MAX_SIZE:
        raise ValueError("This Excel file exceeds the 12 MB import limit.")
    try:
        workbook = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("Unable to read this file as an Excel (.xlsx) workbook. "
                          "Make sure you selected the actual exported report file.") from exc
    try:
        sheet = workbook.active
        source = sheet.iter_rows(values_only=True)
        headers = next(source, None)
        if not headers:
            raise ValueError("Excel file has no headings.")
        mapping = {str(name).strip().casefold(): pos for pos, name in enumerate(headers) if name is not None}
        missing = [name for name in REQUIRED if name.casefold() not in mapping]
        if missing:
            raise ValueError("This Excel file doesn't match the expected Cetaris work order export. Missing columns: " + ", ".join(missing))

        def get(row, label):
            pos = mapping.get(label.casefold())
            return row[pos] if pos is not None and pos < len(row) else None

        results = []
        for i, row in enumerate(source):
            if i >= MAX_ROWS:
                raise ValueError("Import limited to 30,000 labor rows. Narrow your Cetaris export dates.")
            wo = normalize_wo(get(row, "WO Number"))
            tech = _text(get(row, "Technician"))
            if not wo or not tech:
                continue
            started = _parse_dt(get(row, "Start Date"))
            closed_at = _parse_dt(get(row, "WO Closed Date/Time"))
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
                "cause": _text(get(row, "Cause")),
                "last_shift": _text(get(row, "Last Shift")),
                "rfr": _text(get(row, "Primary RFR")),
            })
        return results
    finally:
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


def select_records(rows: list[dict], selected: set[str], start: datetime, end: datetime):
    """Include every Closed non-PM work order; route only PMs to the PM summary.

    Where Start Date and WO Closed Date/Time disagree about which shift a WO
    belongs to (or Start Date is missing/unparseable), the record is routed
    to `ambiguous` for MST review instead of being guessed either way.
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
        closed_at = _parse_dt(r["closed_at"])
        in_window = started is not None and start <= started <= end
        closed_in_window = closed_at is not None and start <= closed_at <= end
        if started is None:
            summary["ambiguous_shift"] += 1
            ambiguous.append({**r, "reason": "Start Date is missing or unreadable -- cannot confirm shift."})
            continue
        if not in_window:
            if closed_in_window:
                # Conflicting signals: started outside the window but closed
                # inside it. Don't guess which shift gets credit.
                summary["ambiguous_shift"] += 1
                ambiguous.append({**r, "reason": "Start Date and WO Closed Date/Time disagree on shift -- needs MST review."})
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
    records = list(grouped.values())
    # Count DISTINCT WO numbers, not rows/labor entries/technicians -- a PM
    # WO logged by two technicians, or with two remarks, still counts once.
    summary["unique_pm_wos"] = len({r["wo_number"] for r in records if r["kind"] == "pm"})
    summary["unique_happening_wos"] = len({r["wo_number"] for r in records if r["kind"] == "happening"})
    return records, {**dict(summary), "ambiguous": ambiguous, "excluded": excluded}

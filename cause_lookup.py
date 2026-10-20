"""Parse the Cetaris "cause" export (WO Number + Repair Description) into a
WO Number -> cause text lookup.

This is a SEPARATE file from cetaris_parser.py on purpose. The timecard
import (asset id + tech remarks, keyed by WO Number) already works and is
left completely untouched. This module only adds the second half of the
join: "fill in the gaps" by matching WO Number against whatever the cause
file calls the fault/repair description.

Supports .csv and .xlsx / .xls exports, same as cetaris_parser.py.
"""
import csv
import io

# Any of these headers (checked in order) counts as the cause text for a row.
_CAUSE_FIELDS = ("Repair Description", "RFR")

# RFR values that mean "Preventive Maintenance" -- these WOs are counted,
# never pulled into shift happenings. This mirrors _PM_WORK_ACCOMPLISHED in
# cetaris_parser.py (same rule, independent signal): the timecard file's
# Work Accomplished field is the primary PM filter and already keeps these
# WOs out of `entries`/happenings entirely; this is a second, independent
# check on the cause file's own RFR classification so a PM row can never
# hand its cause text to a happening even if the two files' codes ever
# disagree on what counts as PM.
_PM_RFR_VALUES = {"preventive maintenance", "pm"}

# Headers that mean "this is a timecard file, not a cause file" -- if this
# shows up, looks_like_cause_file() returns False so we never mis-route a
# normal timecard upload through the cause-only path.
_TIMECARD_ONLY_HEADER = "Tech Remarks"


def _rows_from_xlsx(content: bytes) -> list[dict]:
    """Extract rows from an Excel (.xlsx/.xls) file as a list of dicts."""
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    result = []
    for row in rows[1:]:
        result.append({
            headers[i]: v
            for i, v in enumerate(row)
            if i < len(headers)
        })
    return result


def _rows_from_csv(content: bytes) -> list[dict]:
    """Extract rows from a CSV file as a list of dicts."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)


def _is_xlsx(content: bytes) -> bool:
    """Detect xlsx by magic bytes (PK zip header)."""
    return content[:4] == b"PK\x03\x04"


def _rows(content: bytes | str) -> list[dict]:
    if isinstance(content, str):
        content = content.encode("utf-8-sig")
    return _rows_from_xlsx(content) if _is_xlsx(content) else _rows_from_csv(content)


def normalize_wo(value) -> str:
    """'28017126.0' / 28017126.0 / '28017126' -> '28017126'.

    PUBLIC on purpose: cetaris_parser.py's timecard import must use this
    exact same normalization for its WO Number column, or an .xlsx export
    (where Excel stores WO Number as a numeric cell, e.g. 28017126.0)
    produces a happening keyed by '28017126.0' that can never match this
    module's cause lookup keyed by '28017126' -- the cause silently never
    shows up in the report, with no error anywhere. See cetaris_parser.py
    for the matching call site.
    """
    text = str(value).strip() if value is not None else ""
    if text.endswith(".0"):
        text = text[:-2]
    return text


# Old private name kept as an alias in case anything else imported it directly.
_normalize_wo = normalize_wo


def _clean_text(value) -> str:
    text = str(value).strip() if value is not None else ""
    return "" if text.lower() in ("", "nan", "none") else text.replace("\n", " ").strip()


def looks_like_cause_file(content: bytes | str) -> bool:
    """True if this upload is the cause/repair-description export, not a
    timecard export. Keeps the two file types from ever crossing paths in
    the upload handler.
    """
    try:
        rows = _rows(content)
    except Exception:
        return False
    if not rows:
        return False
    headers = set(rows[0].keys())
    if _TIMECARD_ONLY_HEADER in headers:
        return False
    return "WO Number" in headers and any(f in headers for f in _CAUSE_FIELDS)


def _is_pm_rfr(row: dict) -> bool:
    """True if this cause-file row's RFR marks it Preventive Maintenance."""
    rfr = _clean_text(row.get("RFR")).lower()
    return rfr in _PM_RFR_VALUES


def parse_cause_file(content: bytes | str) -> dict[str, str]:
    """Parse a cause-description export into {wo_number: cause_text}.

    Prefers 'Repair Description'; falls back to 'RFR' if that column is
    blank for a given row. Rows without a usable WO Number or cause text
    are skipped. Preventive Maintenance rows (per RFR) are always skipped --
    PMs are counted, never pulled into shift happenings. See _PM_RFR_VALUES.
    """
    lookup: dict[str, str] = {}
    for row in _rows(content):
        wo_number = _normalize_wo(row.get("WO Number"))
        if not wo_number or _is_pm_rfr(row):
            continue

        cause = ""
        for field in _CAUSE_FIELDS:
            cause = _clean_text(row.get(field))
            if cause:
                break

        if cause:
            lookup[wo_number] = cause

    return lookup

"""Parse Cetaris CMMS CSV or Excel exports into grouped work order data.

Only rows with both an Asset # and WO Number are kept.
Indirect Labor and Unallocated rows are discarded.
Rows tagged as Preventive Maintenance (Work Accomplished = "Prev Maintenance"
or similar) are separated out — counted but NOT added to shift happenings.
Multiple rows sharing the same WO Number are merged — all remarks collected.
Supports .csv and .xlsx / .xls exports from Cetaris.
"""
import csv
import io
from dataclasses import dataclass, field


@dataclass
class ImportedWO:
    """A single work order aggregated from one or more Cetaris CSV rows."""
    wo_number: str
    asset_id: str
    remarks: list[str] = field(default_factory=list)


@dataclass
class ParseResult:
    """Full result of a Cetaris file parse.

    Attributes:
        work_orders:  Non-PM work orders to import as shift happenings.
        pm_count:     Number of Preventive Maintenance WOs found (excluded
                      from work_orders — shown as a summary entry instead).
        pm_wo_numbers: WO numbers of the PM entries (for traceability).
        skipped:      Rows with no Asset/WO or Indirect Labor / Unallocated.
    """
    work_orders:   list[ImportedWO]
    pm_count:      int
    pm_wo_numbers: list[str]
    skipped:       int


# Statuses that mean "nothing to import here"
_SKIP_STATUSES = {"indirect labor", "unallocated", ""}

# Work Accomplished values that indicate a Preventive Maintenance WO.
# Cetaris exports these as "Prev Maintenance" — keep the set lowercase.
_PM_WORK_ACCOMPLISHED = {
    "prev maintenance",
    "preventive maintenance",
    "pm",
    "pm - preventive maintenance",
}


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
            headers[i]: (str(v).strip() if v is not None else "")
            for i, v in enumerate(row)
            if i < len(headers)
        })
    return result


def _rows_from_csv(content: bytes) -> list[dict]:
    """Extract rows from a CSV file as a list of dicts."""
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    return [
        {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        for row in reader
    ]


def _is_xlsx(content: bytes) -> bool:
    """Detect xlsx by magic bytes (PK zip header)."""
    return content[:4] == b'PK\x03\x04'


def _is_pm(row: dict) -> bool:
    """Return True if the row's Work Accomplished tag marks it as PM."""
    wa = row.get("Work Accomplished", "").strip().lower()
    return wa in _PM_WORK_ACCOMPLISHED


def parse_cetaris(content: bytes | str) -> ParseResult:
    """Parse a Cetaris CSV or Excel export and return a full ParseResult.

    PM work orders are separated out from regular happenings so the caller
    can create a single summary entry rather than individual PM entries.

    Args:
        content: Raw bytes of a .csv or .xlsx file, or a string for CSV.

    Returns:
        ParseResult with non-PM work_orders, pm_count, pm_wo_numbers, skipped.
    """
    if isinstance(content, str):
        content = content.encode("utf-8-sig")

    rows = _rows_from_xlsx(content) if _is_xlsx(content) else _rows_from_csv(content)

    wo_map:    dict[str, ImportedWO] = {}
    pm_wos:    dict[str, str]        = {}  # wo_number -> asset_id
    skipped    = 0

    for row in rows:
        asset_id  = row.get("Asset #", "")
        wo_number = row.get("WO Number", "")
        status    = row.get("Status", "").lower()

        if not asset_id or not wo_number:
            skipped += 1
            continue
        if status in _SKIP_STATUSES:
            skipped += 1
            continue

        # PM work orders: count separately, don't add to happenings
        if _is_pm(row):
            pm_wos[wo_number] = asset_id
            continue

        remark = row.get("Tech Remarks", "")
        if wo_number not in wo_map:
            wo_map[wo_number] = ImportedWO(wo_number=wo_number, asset_id=asset_id)
        if remark and remark not in wo_map[wo_number].remarks:
            wo_map[wo_number].remarks.append(remark)

    return ParseResult(
        work_orders=sorted(wo_map.values(), key=lambda w: w.wo_number),
        pm_count=len(pm_wos),
        pm_wo_numbers=sorted(pm_wos.keys()),
        skipped=skipped,
    )


# ---------------------------------------------------------------------------
# Legacy shims — kept so existing call-sites don't break during transition
# ---------------------------------------------------------------------------

def parse_cetaris_csv(content: bytes | str) -> list[ImportedWO]:
    """Legacy shim: returns only non-PM work orders. Prefer parse_cetaris()."""
    return parse_cetaris(content).work_orders


def count_skipped(content: bytes | str) -> int:
    """Legacy shim: returns the skipped row count. Prefer parse_cetaris()."""
    return parse_cetaris(content).skipped

"""Parse Cetaris CMMS CSV or Excel exports into grouped work order data.

Only rows with both an Asset # and WO Number are kept.
Indirect Labor and Unallocated rows are discarded.
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


# Statuses that mean "nothing to import here"
_SKIP_STATUSES = {"indirect labor", "unallocated", ""}


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


def parse_cetaris_csv(content: bytes | str) -> list[ImportedWO]:
    """Parse a Cetaris CSV or Excel export and return grouped work orders.

    Args:
        content: Raw bytes of a .csv or .xlsx file, or a string for CSV.

    Returns:
        List of ImportedWO objects sorted by WO number.
        Duplicate remarks within the same WO are deduplicated.
    """
    if isinstance(content, str):
        content = content.encode("utf-8-sig")

    rows = _rows_from_xlsx(content) if _is_xlsx(content) else _rows_from_csv(content)

    wo_map: dict[str, ImportedWO] = {}

    for row in rows:
        asset_id  = row.get("Asset #", "")
        wo_number = row.get("WO Number", "")
        status    = row.get("Status", "").lower()
        remark    = row.get("Tech Remarks", "")

        if not asset_id or not wo_number:
            continue
        if status in _SKIP_STATUSES:
            continue

        if wo_number not in wo_map:
            wo_map[wo_number] = ImportedWO(wo_number=wo_number, asset_id=asset_id)

        if remark and remark not in wo_map[wo_number].remarks:
            wo_map[wo_number].remarks.append(remark)

    return sorted(wo_map.values(), key=lambda w: w.wo_number)


def count_skipped(content: bytes | str) -> int:
    """Return how many rows were skipped (Indirect Labor / Unallocated / no WO)."""
    if isinstance(content, str):
        content = content.encode("utf-8-sig")

    rows = _rows_from_xlsx(content) if _is_xlsx(content) else _rows_from_csv(content)
    skipped = 0
    for row in rows:
        asset_id  = row.get("Asset #", "")
        wo_number = row.get("WO Number", "")
        status    = row.get("Status", "").lower()
        if not asset_id or not wo_number or status in _SKIP_STATUSES:
            skipped += 1
    return skipped

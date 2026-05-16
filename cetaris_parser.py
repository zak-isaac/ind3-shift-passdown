"""Parse Cetaris CMMS CSV exports into grouped work order data.

Only rows with both an Asset # and WO Number are kept.
Indirect Labor and Unallocated rows are discarded.
Multiple rows sharing the same WO Number are merged — all remarks collected.
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


def parse_cetaris_csv(content: bytes | str) -> list[ImportedWO]:
    """Parse a Cetaris CSV export and return grouped work orders.

    Args:
        content: Raw bytes or string of the CSV file.

    Returns:
        List of ImportedWO objects sorted by WO number.
        Duplicate remarks within the same WO are deduplicated.
    """
    if isinstance(content, bytes):
        # utf-8-sig handles the BOM that Excel/Cetaris sometimes adds
        content = content.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(content))

    wo_map: dict[str, ImportedWO] = {}

    for row in reader:
        # Strip whitespace from every key and value
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}

        asset_id  = row.get("Asset #", "")
        wo_number = row.get("WO Number", "")
        status    = row.get("Status", "").lower()
        remark    = row.get("Tech Remarks", "")

        # Must have both asset and WO to be useful
        if not asset_id or not wo_number:
            continue

        # Drop everything that isn't a real maintenance WO
        if status in _SKIP_STATUSES:
            continue

        if wo_number not in wo_map:
            wo_map[wo_number] = ImportedWO(wo_number=wo_number, asset_id=asset_id)

        # Collect remark — skip blanks and exact duplicates
        if remark and remark not in wo_map[wo_number].remarks:
            wo_map[wo_number].remarks.append(remark)

    return sorted(wo_map.values(), key=lambda w: w.wo_number)


def count_skipped(content: bytes | str) -> int:
    """Return how many rows were skipped (Indirect Labor / Unallocated / no WO)."""
    if isinstance(content, bytes):
        content = content.decode("utf-8-sig", errors="replace")

    reader = csv.DictReader(io.StringIO(content))
    skipped = 0
    for row in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in row.items()}
        asset_id  = row.get("Asset #", "")
        wo_number = row.get("WO Number", "")
        status    = row.get("Status", "").lower()
        if not asset_id or not wo_number or status in _SKIP_STATUSES:
            skipped += 1
    return skipped

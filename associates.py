"""associates.py — Load Drax associate roster from the Excel file at startup."""

from __future__ import annotations
import os
from pathlib import Path
import pandas as pd

# Canonical Drax file location (double-space is intentional — that's the real filename)
_DEFAULT_XLSX = Path(r"C:\Users\ins0002\OneDrive - Walmart Inc\Drax  Associates.xlsx")

MANAGER_CODE = "8972"

_MANAGER_ENTRY: dict = {
    "display_name": "Manager",
    "full_name":    "Manager (Preview)",
    "assoc_num":    MANAGER_CODE,
    "default_id":   "mgr",
    "shift":        "ALL",
    "is_manager":   True,
    "is_mst":       False,
}

# Drax column names (header row 0)
_COL_NAME      = "Name"
_COL_NUM       = "Assoc. #"
_COL_ID        = "Default ID"
_COL_SHIFT     = "Shift"
_COL_JOB_TITLE = "Job Title"   # 'MST' → auto-flagged as is_mst


def _display_name(full: str) -> str:
    """'ALEXANDER CARR' → 'Alexander C'"""
    parts = full.strip().split()
    if not parts:
        return full.title()
    first = parts[0].capitalize()
    last  = parts[-1][0].upper() if len(parts) > 1 else ""
    return f"{first} {last}".strip()


def resolve_drax_path() -> Path:
    """Return the effective Drax file path (env var override or hardcoded default)."""
    env = os.environ.get("ASSOCIATES_PATH", "")
    return Path(env) if env else _DEFAULT_XLSX


def remove_from_drax(assoc_num: str) -> bool:
    """Delete the row matching assoc_num from the Drax Excel file.
    Returns True on success, False if the file is missing or the write fails.
    Does NOT raise — callers should check the return value and warn the user.
    """
    import openpyxl

    fp = resolve_drax_path()
    if not fp.exists():
        return False
    try:
        wb = openpyxl.load_workbook(fp)
        ws = wb.active
        target = str(assoc_num).strip()
        for row in ws.iter_rows(min_row=2):          # row 1 is the header
            cell = row[1]                            # column B = Assoc. #
            if cell.value is None:
                continue
            # Values may be int, float, or str in Excel
            try:
                cell_str = str(int(float(cell.value)))
            except (ValueError, TypeError):
                cell_str = str(cell.value).strip()
            if cell_str == target:
                ws.delete_rows(cell.row)
                wb.save(fp)
                print(f"[drax] Removed assoc {assoc_num} from {fp.name}", flush=True)
                return True
        print(f"[drax] assoc {assoc_num} not found in {fp.name} (row may already be gone)", flush=True)
        return True  # not an error — already absent
    except PermissionError:
        print(f"[drax] Cannot write {fp.name} — file may be open in Excel/OneDrive", flush=True)
        return False
    except Exception as exc:
        print(f"[drax] Error writing {fp.name}:", flush=True)
        return False


def load_associates(path: str | Path | None = None) -> dict[str, dict]:
    """Return dict keyed by associate-number string.
    Always includes the manager bypass entry.
    Associates with Job Title == 'MST' are auto-flagged as is_mst.
    """
    roster: dict[str, dict] = {}

    fp = Path(path) if path else resolve_drax_path()
    if fp.exists():
        try:
            df = pd.read_excel(fp, header=0)  # row 0 is the real header

            for _, row in df.iterrows():
                raw_name = row.get(_COL_NAME)
                raw_num  = row.get(_COL_NUM)
                if not isinstance(raw_name, str) or pd.isna(raw_num):
                    continue
                key       = str(int(raw_num))
                job_title = str(row.get(_COL_JOB_TITLE, "")).strip()
                shift_val = row.get(_COL_SHIFT)
                id_val    = row.get(_COL_ID)
                roster[key] = {
                    "display_name": _display_name(raw_name),
                    "full_name":    raw_name.title(),
                    "assoc_num":    key,
                    "default_id":   str(id_val) if not pd.isna(id_val) else "",
                    "shift":        str(shift_val) if not pd.isna(shift_val) else "",
                    "status":       job_title or "Active",
                    "is_manager":   False,
                    "is_mst":       job_title.upper() == "MST",
                }
            print(f"[associates] Loaded {len(roster)} associates from {fp.name}", flush=True)
        except Exception as exc:
            print(f"[associates] ERROR reading {fp}: {exc}", flush=True)
    else:
        print(f"[associates] Drax file not found at: {fp}", flush=True)

    roster[MANAGER_CODE] = _MANAGER_ENTRY
    return roster

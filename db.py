"""Database layer - shifts, entries, reports, OOS, associates, and settings.

DB_PATH is set at startup by main.py via set_db_path() so each facility
gets its own isolated SQLite file under dbs/{slug}.db.
"""
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

# Overridden at startup — do NOT import this directly; use _conn()
_DB_PATH = Path(__file__).parent / "passdowns.db"


def set_db_path(path: Path) -> None:
    """Point all future connections at a different file (called on facility switch)."""
    global _DB_PATH
    _DB_PATH = path


def get_db_path() -> Path:
    return _DB_PATH


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(_DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db() -> None:
    with _conn() as c:
        c.executescript("""
            CREATE TABLE IF NOT EXISTS shifts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                shift_type  TEXT    NOT NULL,
                lead_name   TEXT    NOT NULL,
                created_at  TEXT    NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS entries (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id    INTEGER NOT NULL REFERENCES shifts(id),
                tech_name   TEXT    NOT NULL,
                unit        TEXT    NOT NULL,
                issue       TEXT    NOT NULL,
                action      TEXT    NOT NULL,
                result      TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id     INTEGER NOT NULL REFERENCES shifts(id),
                generated_at TEXT    NOT NULL,
                content      TEXT    NOT NULL
            );
        """)

        # Migrations - safe to run on existing databases
        existing_report_cols = {row[1] for row in c.execute("PRAGMA table_info(reports)")}
        if "report_json" not in existing_report_cols:
            c.execute("ALTER TABLE reports ADD COLUMN report_json TEXT DEFAULT NULL")

        existing_entry_cols = {row[1] for row in c.execute("PRAGMA table_info(entries)")}
        if "priority" not in existing_entry_cols:
            c.execute("ALTER TABLE entries ADD COLUMN priority TEXT NOT NULL DEFAULT 'monitor'")
        if "carry_over" not in existing_entry_cols:
            c.execute("ALTER TABLE entries ADD COLUMN carry_over INTEGER NOT NULL DEFAULT 0")
        # CMMS import tracking — added for Cetaris auto-import feature
        if "source" not in existing_entry_cols:
            c.execute("ALTER TABLE entries ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
        if "wo_number" not in existing_entry_cols:
            c.execute("ALTER TABLE entries ADD COLUMN wo_number TEXT NOT NULL DEFAULT ''")
        if "raw_remarks" not in existing_entry_cols:
            c.execute("ALTER TABLE entries ADD COLUMN raw_remarks TEXT NOT NULL DEFAULT '[]'")
        if "excluded" not in existing_entry_cols:
            c.execute("ALTER TABLE entries ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0")

        existing_shift_cols = {row[1] for row in c.execute("PRAGMA table_info(shifts)")}
        if "notes" not in existing_shift_cols:
            c.execute("ALTER TABLE shifts ADD COLUMN notes TEXT NOT NULL DEFAULT ''")


        c.execute("""
            CREATE TABLE IF NOT EXISTS oos_units (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                unit     TEXT NOT NULL UNIQUE,
                added_at TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS rts_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id   INTEGER REFERENCES shifts(id),
                unit       TEXT NOT NULL,
                removed_at TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS ot_codes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                code            TEXT    NOT NULL UNIQUE,
                assoc_num       TEXT    NOT NULL,
                assoc_name      TEXT    NOT NULL,
                assigned_shift  TEXT    NOT NULL,
                requested_shift TEXT    NOT NULL,
                created_at      TEXT    NOT NULL,
                expires_at      TEXT    NOT NULL,
                used            INTEGER NOT NULL DEFAULT 0
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS tech_done (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id  INTEGER NOT NULL REFERENCES shifts(id),
                tech_name TEXT    NOT NULL,
                marked_at TEXT    NOT NULL,
                UNIQUE(shift_id, tech_name)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS pm_wos (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id   INTEGER NOT NULL REFERENCES shifts(id),
                wo_number  TEXT    NOT NULL,
                asset_id   TEXT    NOT NULL DEFAULT '',
                tech_name  TEXT    NOT NULL DEFAULT '',
                created_at TEXT    NOT NULL,
                UNIQUE(shift_id, wo_number)
            )
        """)

        c.execute("""
            CREATE TABLE IF NOT EXISTS associates (
                assoc_num    TEXT PRIMARY KEY,
                full_name    TEXT NOT NULL,
                display_name TEXT NOT NULL,
                default_id   TEXT NOT NULL DEFAULT '',
                shift        TEXT NOT NULL DEFAULT '',
                status       TEXT NOT NULL DEFAULT 'Active',
                is_manager   INTEGER NOT NULL DEFAULT 0
            )
        """)

        # Associates migrations — must run AFTER CREATE TABLE above
        existing_assoc_cols = {row[1] for row in c.execute("PRAGMA table_info(associates)")}
        if "is_mst" not in existing_assoc_cols:
            c.execute("ALTER TABLE associates ADD COLUMN is_mst INTEGER NOT NULL DEFAULT 0")

        # LOTO verification fields on shifts
        existing_shift_cols = {row[1] for row in c.execute("PRAGMA table_info(shifts)")}
        if "loto_verified" not in existing_shift_cols:
            c.execute("ALTER TABLE shifts ADD COLUMN loto_verified INTEGER NOT NULL DEFAULT 0")
        if "loto_missing_keys" not in existing_shift_cols:
            c.execute("ALTER TABLE shifts ADD COLUMN loto_missing_keys TEXT NOT NULL DEFAULT ''")

        # PM files table
        c.execute("""
            CREATE TABLE IF NOT EXISTS pm_files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id    INTEGER NOT NULL REFERENCES shifts(id),
                filename    TEXT    NOT NULL,
                stored_name TEXT    NOT NULL,
                uploaded_by TEXT    NOT NULL DEFAULT '',
                uploaded_at TEXT    NOT NULL
            )
        """)

        # Happenings files — MST-uploaded supplement to tech-logged entries
        c.execute("""
            CREATE TABLE IF NOT EXISTS happenings_files (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id    INTEGER NOT NULL REFERENCES shifts(id),
                filename    TEXT    NOT NULL,
                stored_name TEXT    NOT NULL,
                uploaded_by TEXT    NOT NULL DEFAULT '',
                uploaded_at TEXT    NOT NULL
            )
        """)

        # Pending PM rows (parsed from uploaded Cetaris PM report)
        c.execute("""
            CREATE TABLE IF NOT EXISTS pending_pms (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                shift_id     INTEGER NOT NULL REFERENCES shifts(id),
                wo_number    TEXT NOT NULL,
                wo_date      TEXT NOT NULL DEFAULT '',
                pm_due_date  TEXT NOT NULL DEFAULT '',
                promise_date TEXT NOT NULL DEFAULT '',
                asset_num    TEXT NOT NULL DEFAULT '',
                uploaded_at  TEXT NOT NULL
            )
        """)

        # NOTE: entries are intentionally kept forever for cross-shift history.


# ---------------------------------------------------------------------------
# Shifts
# ---------------------------------------------------------------------------

def start_shift(date: str, shift_type: str, lead_name: str) -> int:
    """Open a new shift. Up to 2 can run simultaneously during overlap windows.
    Raises ValueError('max_active') if 2 are already running.
    """
    with _conn() as c:
        active_count = c.execute(
            "SELECT COUNT(*) FROM shifts WHERE is_active=1"
        ).fetchone()[0]
        if active_count >= 2:
            raise ValueError("max_active")
        cur = c.execute(
            "INSERT INTO shifts (date, shift_type, lead_name, created_at, is_active)"
            " VALUES (?,?,?,?,1)",
            (date, shift_type, lead_name, _now()),
        )
        return cur.lastrowid


def get_active_shifts() -> list[dict]:
    """Return all currently active shifts (0-2), newest first."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM shifts WHERE is_active=1 ORDER BY id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_shift(shift_id: int) -> Optional[dict]:
    """Return a single shift by id, or None if not found."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM shifts WHERE id=?", (shift_id,)
        ).fetchone()
    return dict(row) if row else None


def get_active_shift() -> Optional[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM shifts WHERE is_active=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else None


def close_shift(shift_id: int) -> None:
    """Mark shift inactive — entries are kept for cross-shift history."""
    with _conn() as c:
        c.execute("UPDATE shifts SET is_active=0 WHERE id=?", (shift_id,))


def get_shift_notes(shift_id: int) -> str:
    with _conn() as c:
        row = c.execute("SELECT notes FROM shifts WHERE id=?", (shift_id,)).fetchone()
    return row[0] if row else ""


# ── LOTO verification ─────────────────────────────────────────────────────

def save_loto(shift_id: int, verified: bool, missing_keys: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE shifts SET loto_verified=?, loto_missing_keys=? WHERE id=?",
            (1 if verified else 0, missing_keys.strip(), shift_id),
        )


def get_loto(shift_id: int) -> dict:
    with _conn() as c:
        row = c.execute(
            "SELECT loto_verified, loto_missing_keys FROM shifts WHERE id=?",
            (shift_id,)
        ).fetchone()
    if not row:
        return {"loto_verified": 0, "loto_missing_keys": ""}
    return {"loto_verified": row[0], "loto_missing_keys": row[1]}


# ── PM files ────────────────────────────────────────────────────────────

def save_pm_file(shift_id: int, filename: str, stored_name: str, uploaded_by: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO pm_files (shift_id, filename, stored_name, uploaded_by, uploaded_at)"
            " VALUES (?,?,?,?,?)",
            (shift_id, filename, stored_name, uploaded_by, _now()),
        )
        return cur.lastrowid


def get_pm_files(shift_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM pm_files WHERE shift_id=? ORDER BY id DESC",
            (shift_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_pm_file(file_id: int) -> Optional[str]:
    """Delete DB record; returns stored_name so caller can remove the file too."""
    with _conn() as c:
        row = c.execute("SELECT stored_name FROM pm_files WHERE id=?", (file_id,)).fetchone()
        if row:
            c.execute("DELETE FROM pm_files WHERE id=?", (file_id,))
            return row[0]
    return None


# ── Happenings files ──────────────────────────────────────────────────────────────

def save_happenings_file(shift_id: int, filename: str, stored_name: str, uploaded_by: str) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO happenings_files (shift_id, filename, stored_name, uploaded_by, uploaded_at)"
            " VALUES (?,?,?,?,?)",
            (shift_id, filename, stored_name, uploaded_by, _now()),
        )
        return cur.lastrowid


def get_happenings_files(shift_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM happenings_files WHERE shift_id=? ORDER BY id DESC",
            (shift_id,)
        ).fetchall()
    return [dict(r) for r in rows]


def delete_happenings_file(file_id: int) -> Optional[str]:
    """Delete DB record; returns stored_name so caller can remove the file too."""
    with _conn() as c:
        row = c.execute("SELECT stored_name FROM happenings_files WHERE id=?", (file_id,)).fetchone()
        if row:
            c.execute("DELETE FROM happenings_files WHERE id=?", (file_id,))
            return row[0]
    return None


# ── Pending PMs (parsed from Cetaris export) ────────────────────────────────────

def save_pending_pms(shift_id: int, rows: list[dict]) -> None:
    """Replace all pending PM rows for this shift with fresh parsed data."""
    with _conn() as c:
        c.execute("DELETE FROM pending_pms WHERE shift_id=?", (shift_id,))
        now = _now()
        c.executemany(
            "INSERT INTO pending_pms"
            " (shift_id, wo_number, wo_date, pm_due_date, promise_date, asset_num, uploaded_at)"
            " VALUES (?,?,?,?,?,?,?)",
            [(shift_id, r["wo_number"], r["wo_date"], r["pm_due_date"],
              r["promise_date"], r["asset_num"], now) for r in rows],
        )


def clear_pending_pms(shift_id: int) -> None:
    """Remove all extracted PM rows for a shift (called when the PM file is deleted)."""
    with _conn() as c:
        c.execute("DELETE FROM pending_pms WHERE shift_id=?", (shift_id,))


def get_pending_pms(shift_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM pending_pms WHERE shift_id=? ORDER BY asset_num, pm_due_date",
            (shift_id,)
        ).fetchall()
    return [dict(r) for r in rows]


    with _conn() as c:
        row = c.execute("SELECT notes FROM shifts WHERE id=?", (shift_id,)).fetchone()
    return row[0] if row else ""


def list_past_shifts(limit: int = 10) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM shifts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [dict(r) for r in rows]


def list_past_shifts_with_counts(limit: int = 50) -> list[dict]:
    """Past shifts with entry + report counts — orphaned shifts (no entries, no reports) excluded."""
    with _conn() as c:
        rows = c.execute("""
            SELECT s.id, s.date, s.shift_type, s.lead_name,
                   s.created_at, s.is_active,
                   COUNT(DISTINCT e.id)  AS entry_count,
                   COUNT(DISTINCT r.id)  AS report_count
            FROM shifts s
            LEFT JOIN entries e ON e.shift_id = s.id
            LEFT JOIN reports r ON r.shift_id = s.id
            GROUP BY s.id
            HAVING entry_count > 0 OR report_count > 0
            ORDER BY s.id DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_analytics() -> dict:
    """Return all data needed for the weekly analytics dashboard."""
    with _conn() as c:
        # Top 15 most-worked assets (all time, non-PM, non-excluded)
        top_assets = c.execute("""
            SELECT unit, COUNT(*) AS cnt
            FROM entries
            WHERE unit != '' AND source != 'pm' AND excluded = 0
            GROUP BY unit ORDER BY cnt DESC LIMIT 15
        """).fetchall()

        # Assets appearing in multiple DISTINCT shifts (repeat offenders)
        repeat_assets = c.execute("""
            SELECT unit, COUNT(DISTINCT shift_id) AS shift_cnt, COUNT(*) AS total_wo
            FROM entries
            WHERE unit != '' AND source != 'pm' AND excluded = 0
            GROUP BY unit HAVING shift_cnt > 1
            ORDER BY shift_cnt DESC, total_wo DESC LIMIT 10
        """).fetchall()

        # WO count per shift (last 30 shifts, non-PM)
        wo_by_shift = c.execute("""
            SELECT s.date, s.shift_type, COUNT(e.id) AS wo_count,
                   SUM(CASE WHEN e.priority='down' THEN 1 ELSE 0 END) AS down_count
            FROM shifts s
            LEFT JOIN entries e ON e.shift_id = s.id
                AND e.source != 'pm' AND e.excluded = 0
            WHERE s.is_active = 0
            GROUP BY s.id ORDER BY s.id DESC LIMIT 30
        """).fetchall()

        # PM count per shift (last 30)
        pm_by_shift = c.execute("""
            SELECT s.date, s.shift_type, COUNT(p.id) AS pm_count
            FROM shifts s
            LEFT JOIN pm_wos p ON p.shift_id = s.id
            WHERE s.is_active = 0
            GROUP BY s.id ORDER BY s.id DESC LIMIT 30
        """).fetchall()

        # Priority breakdown totals
        priority_totals = c.execute("""
            SELECT priority, COUNT(*) AS cnt FROM entries
            WHERE excluded = 0 AND source != 'pm'
            GROUP BY priority
        """).fetchall()

        # Top technicians by entry count
        top_techs = c.execute("""
            SELECT tech_name, COUNT(*) AS cnt
            FROM entries WHERE excluded = 0
            GROUP BY tech_name ORDER BY cnt DESC LIMIT 10
        """).fetchall()

        # Total shift & entry counts
        totals = c.execute("""
            SELECT
                COUNT(DISTINCT s.id)  AS total_shifts,
                COUNT(DISTINCT e.id)  AS total_entries,
                (SELECT COUNT(*) FROM pm_wos) AS total_pms
            FROM shifts s
            LEFT JOIN entries e ON e.shift_id = s.id
        """).fetchone()

    return {
        "top_assets":      [dict(r) for r in top_assets],
        "repeat_assets":   [dict(r) for r in repeat_assets],
        "wo_by_shift":     [dict(r) for r in reversed(wo_by_shift)],
        "pm_by_shift":     [dict(r) for r in reversed(pm_by_shift)],
        "priority_totals": {r["priority"]: r["cnt"] for r in [dict(r) for r in priority_totals]},
        "top_techs":       [dict(r) for r in top_techs],
        "totals":          dict(totals) if totals else {},
    }

    """Past shifts with entry + report counts — orphaned shifts (no entries, no reports) excluded."""
    with _conn() as c:
        rows = c.execute("""
            SELECT s.id, s.date, s.shift_type, s.lead_name,
                   s.created_at, s.is_active,
                   COUNT(DISTINCT e.id)  AS entry_count,
                   COUNT(DISTINCT r.id)  AS report_count
            FROM shifts s
            LEFT JOIN entries e ON e.shift_id = s.id
            LEFT JOIN reports r ON r.shift_id = s.id
            GROUP BY s.id
            HAVING entry_count > 0 OR report_count > 0
            ORDER BY s.id DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def purge_orphaned_shifts() -> int:
    """Delete inactive shifts that have zero entries and zero reports.
    These were started but abandoned before PIN verification under the old flow.
    Safe to call at startup — active shifts are never touched.
    Returns count of purged shifts.
    """
    with _conn() as c:
        ids = [
            r[0] for r in c.execute("""
                SELECT s.id FROM shifts s
                WHERE s.is_active = 0
                  AND NOT EXISTS (SELECT 1 FROM entries e WHERE e.shift_id = s.id)
                  AND NOT EXISTS (SELECT 1 FROM reports r WHERE r.shift_id = s.id)
            """).fetchall()
        ]
        if ids:
            placeholders = ",".join("?" * len(ids))
            c.execute(f"DELETE FROM shifts WHERE id IN ({placeholders})", ids)
            print(f"[startup] purged {len(ids)} orphaned shift(s): {ids}", flush=True)
    return len(ids) if ids else 0


def get_asset_history(unit: str, limit: int = 40) -> list[dict]:
    """Cross-shift history for a specific asset ID."""
    with _conn() as c:
        rows = c.execute("""
            SELECT e.unit, e.issue, e.tech_name, e.created_at,
                   s.date, s.shift_type, s.lead_name
            FROM entries e JOIN shifts s ON s.id = e.shift_id
            WHERE LOWER(e.unit) = LOWER(?)
            ORDER BY e.created_at DESC LIMIT ?
        """, (unit, limit)).fetchall()
    return [dict(r) for r in rows]


def get_tech_participation(shift_id: int) -> list[dict]:
    """Techs active this shift with entry counts, priority breakdown, and last submission time."""
    with _conn() as c:
        rows = c.execute("""
            SELECT tech_name,
                   COUNT(*)                                          AS entries,
                   MAX(created_at)                                   AS last_entry,
                   SUM(CASE WHEN priority='down'     THEN 1 ELSE 0 END) AS down_count,
                   SUM(CASE WHEN priority='degraded' THEN 1 ELSE 0 END) AS degraded_count,
                   SUM(CASE WHEN priority='monitor'  THEN 1 ELSE 0 END) AS monitor_count,
                   SUM(carry_over)                                   AS carry_count
            FROM entries
            WHERE shift_id = ?
            GROUP BY tech_name
            ORDER BY entries DESC, tech_name
        """, (shift_id,)).fetchall()
    return [dict(r) for r in rows]


def get_weekly_summary(days: int = 28) -> dict:
    """Aggregated summary over the past N days."""
    with _conn() as c:
        shifts_by_date = c.execute("""
            SELECT date, COUNT(*) AS shift_count
            FROM shifts
            WHERE date >= date('now', :offset)
            GROUP BY date ORDER BY date
        """, {"offset": f"-{days} days"}).fetchall()

        top_assets = c.execute("""
            SELECT e.unit,
                   COUNT(*) AS total_reports,
                   SUM(CASE WHEN e.priority='down'     THEN 1 ELSE 0 END) AS down_count,
                   SUM(CASE WHEN e.priority='degraded' THEN 1 ELSE 0 END) AS degraded_count,
                   SUM(CASE WHEN e.priority='monitor'  THEN 1 ELSE 0 END) AS monitor_count
            FROM entries e JOIN shifts s ON s.id = e.shift_id
            WHERE s.date >= date('now', :offset)
            GROUP BY e.unit
            ORDER BY total_reports DESC LIMIT 15
        """, {"offset": f"-{days} days"}).fetchall()

        by_shift_type = c.execute("""
            SELECT s.shift_type,
                   COUNT(DISTINCT s.id)  AS shift_count,
                   COUNT(e.id)           AS entry_count
            FROM shifts s
            LEFT JOIN entries e ON e.shift_id = s.id
            WHERE s.date >= date('now', :offset)
            GROUP BY s.shift_type ORDER BY s.shift_type
        """, {"offset": f"-{days} days"}).fetchall()

        total_shifts  = c.execute("SELECT COUNT(*) FROM shifts").fetchone()[0]
        total_entries = c.execute("SELECT COUNT(*) FROM entries").fetchone()[0]

    return {
        "shifts_by_date": [dict(r) for r in shifts_by_date],
        "top_assets":     [dict(r) for r in top_assets],
        "by_shift_type":  [dict(r) for r in by_shift_type],
        "total_shifts":   total_shifts,
        "total_entries":  total_entries,
        "days":           days,
    }


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def get_asset_stats(shift_id: int) -> list[dict]:
    """Return assets hit this shift, ranked by frequency descending."""
    with _conn() as c:
        rows = c.execute(
            """
            SELECT unit,
                   COUNT(*) AS hits,
                   SUM(CASE WHEN priority='down'     THEN 1 ELSE 0 END) AS down_count,
                   SUM(CASE WHEN priority='degraded' THEN 1 ELSE 0 END) AS degraded_count,
                   SUM(CASE WHEN priority='monitor'  THEN 1 ELSE 0 END) AS monitor_count
            FROM entries
            WHERE shift_id = ?
            GROUP BY unit
            ORDER BY hits DESC, unit
            """,
            (shift_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_entry(shift_id: int, tech_name: str,
              unit: str, issue: str, action: str, result: str,
              priority: str = "monitor") -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO entries (shift_id,tech_name,unit,issue,action,result,priority,created_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (shift_id, tech_name, unit, issue, action, result, priority, _now()),
        )
        return cur.lastrowid


# ---------------------------------------------------------------------------
# CMMS Import (Cetaris auto-import)
# ---------------------------------------------------------------------------

def get_cmms_entry(shift_id: int, wo_number: str, tech_name: str = "") -> Optional[dict]:
    """Return an existing CMMS entry for this WO+shift, optionally filtered by tech."""
    with _conn() as c:
        if tech_name:
            row = c.execute(
                "SELECT * FROM entries "
                "WHERE shift_id=? AND wo_number=? AND tech_name=? AND source='cmms' LIMIT 1",
                (shift_id, wo_number, tech_name),
            ).fetchone()
        else:
            row = c.execute(
                "SELECT * FROM entries WHERE shift_id=? AND wo_number=? AND source='cmms' LIMIT 1",
                (shift_id, wo_number),
            ).fetchone()
    return dict(row) if row else None


def upsert_cmms_entry(
    shift_id: int,
    wo_number: str,
    asset_id: str,
    summary: str,
    raw_remarks_json: str,
    tech_name: str = "CMMS Import",
) -> int:
    """Insert or update a CMMS entry keyed on (shift_id, wo_number, tech_name).

    Different techs working the same WO each get their own row.
    Re-uploading the same file updates that tech's row in place.
    Returns the entry id.
    """
    issue_text = f"WO #{wo_number} \u2014 {summary}"
    with _conn() as c:
        existing = c.execute(
            "SELECT id FROM entries "
            "WHERE shift_id=? AND wo_number=? AND tech_name=? AND source='cmms'",
            (shift_id, wo_number, tech_name),
        ).fetchone()
        if existing:
            c.execute(
                "UPDATE entries SET unit=?, issue=?, raw_remarks=? WHERE id=?",
                (asset_id, issue_text, raw_remarks_json, existing[0]),
            )
            return existing[0]
        cur = c.execute(
            "INSERT INTO entries "
            "(shift_id, tech_name, unit, issue, action, result, priority, "
            " created_at, source, wo_number, raw_remarks) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                shift_id, tech_name, asset_id, issue_text,
                "", "", "monitor", _now(), "cmms", wo_number, raw_remarks_json,
            ),
        )
        return cur.lastrowid


def get_cmms_raw_remarks(shift_id: int, wo_number: str, tech_name: str = "") -> list[str]:
    """Return stored raw remarks for a CMMS entry, scoped to a specific tech."""
    import json
    row = get_cmms_entry(shift_id, wo_number, tech_name)
    if not row:
        return []
    try:
        return json.loads(row.get("raw_remarks", "[]"))
    except (ValueError, TypeError):
        return []


def set_entry_excluded(entry_id: int, excluded: bool) -> None:
    """MST-only: mark an entry as excluded (hidden) from the generated report."""
    with _conn() as c:
        c.execute("UPDATE entries SET excluded=? WHERE id=?", (1 if excluded else 0, entry_id))


def update_entry_issue(entry_id: int, issue: str) -> None:
    """MST-only: overwrite the issue text of an entry."""
    with _conn() as c:
        c.execute("UPDATE entries SET issue=? WHERE id=?", (issue, entry_id))



    with _conn() as c:
        c.execute("DELETE FROM entries WHERE id=?", (entry_id,))


# ---------------------------------------------------------------------------
# PM Work Orders (Preventive Maintenance — counted, not shown as happenings)
# ---------------------------------------------------------------------------

def upsert_pm_wo(
    shift_id: int,
    wo_number: str,
    asset_id: str = "",
    tech_name: str = "",
) -> None:
    """Record a PM work order for this shift. Silently ignores duplicates."""
    with _conn() as c:
        c.execute(
            """
            INSERT OR IGNORE INTO pm_wos (shift_id, wo_number, asset_id, tech_name, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (shift_id, wo_number, asset_id, tech_name, _now()),
        )


def get_pm_count(shift_id: int) -> int:
    """Return the number of unique PM work orders completed this shift."""
    with _conn() as c:
        row = c.execute(
            "SELECT COUNT(*) FROM pm_wos WHERE shift_id = ?",
            (shift_id,),
        ).fetchone()
    return row[0] if row else 0


def set_carry_over(entry_id: int, state: bool) -> None:
    with _conn() as c:
        c.execute("UPDATE entries SET carry_over=? WHERE id=?", (1 if state else 0, entry_id))


def get_carry_over_entries(current_shift_id: int = 0) -> list[dict]:
    """Return carry-over entries from the most recent predecessor shift that
    actually has carry-over entries flagged.

    Skips over empty or accidentally-closed gaps so that, e.g., an empty
    S4 shell between S5 and the real S4 never swallows S5's carry-overs.

    Works in all scenarios:
    - Normal handover (predecessor closed)
    - Overlap window (predecessor still active but lower ID)
    - Empty shells / accidental closes in between
    """
    with _conn() as c:
        if current_shift_id:
            # Walk back from current_shift_id and find the nearest predecessor
            # that actually has at least one carry_over=1 entry.
            row = c.execute(
                """
                SELECT DISTINCT e.shift_id
                FROM entries e
                WHERE e.carry_over = 1
                  AND e.shift_id < ?
                ORDER BY e.shift_id DESC
                LIMIT 1
                """,
                (current_shift_id,),
            ).fetchone()
        else:
            # Legacy / no-context path: most recent shift with carry-over entries
            row = c.execute(
                """
                SELECT DISTINCT e.shift_id
                FROM entries e
                JOIN shifts s ON s.id = e.shift_id
                WHERE e.carry_over = 1 AND s.is_active = 0
                ORDER BY e.shift_id DESC
                LIMIT 1
                """
            ).fetchone()

        if not row:
            return []

        rows = c.execute(
            "SELECT e.*, s.date, s.shift_type, s.lead_name FROM entries e"
            " JOIN shifts s ON s.id = e.shift_id"
            " WHERE e.shift_id=? AND e.carry_over=1 ORDER BY e.created_at",
            (row[0],),
        ).fetchall()
    return [dict(r) for r in rows]


def save_shift_notes(shift_id: int, notes: str) -> None:
    with _conn() as c:
        c.execute("UPDATE shifts SET notes=? WHERE id=?", (notes.strip(), shift_id))


def get_recurring_assets(days: int = 7, min_reports: int = 3) -> set[str]:
    """Asset IDs reported >= min_reports times across the past N days."""
    with _conn() as c:
        rows = c.execute("""
            SELECT e.unit
            FROM entries e JOIN shifts s ON s.id = e.shift_id
            WHERE s.date >= date('now', :offset)
            GROUP BY e.unit
            HAVING COUNT(*) >= :min
        """, {"offset": f"-{days} days", "min": min_reports}).fetchall()
    return {r[0] for r in rows}


def get_entries(shift_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM entries WHERE shift_id=? ORDER BY created_at",
            (shift_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_entry(entry_id: int) -> Optional[dict]:
    """Fetch a single entry by ID."""
    with _conn() as c:
        row = c.execute("SELECT * FROM entries WHERE id=?", (entry_id,)).fetchone()
    return dict(row) if row else None


def delete_entry(entry_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM entries WHERE id=?", (entry_id,))


def update_entry(entry_id: int, unit: str, issue: str, priority: str) -> None:
    with _conn() as c:
        c.execute(
            "UPDATE entries SET unit=?, issue=?, priority=? WHERE id=?",
            (unit.strip(), issue.strip(), priority, entry_id),
        )


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------

def save_report(shift_id: int, content: str,
               report_json: str | None = None) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO reports (shift_id, generated_at, content, report_json)"
            " VALUES (?,?,?,?)",
            (shift_id, _now(), content, report_json),
        )
        return cur.lastrowid


def get_last_report_for_shift(shift_id: int) -> Optional[dict]:
    """Return the most recently generated report for a given shift."""
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM reports WHERE shift_id=? ORDER BY id DESC LIMIT 1",
            (shift_id,)
        ).fetchone()
    return dict(row) if row else None


def list_reports(limit: int = 15) -> list[dict]:
    with _conn() as c:
        rows = c.execute("""
            SELECT r.id, r.generated_at, r.content,
                   s.date, s.shift_type, s.lead_name
            FROM reports r JOIN shifts s ON s.id = r.shift_id
            ORDER BY r.id DESC LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


def get_report(report_id: int) -> Optional[dict]:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM reports WHERE id=?", (report_id,)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# OOS - persistent across shifts until MST removes them
# ---------------------------------------------------------------------------

def get_oos_units() -> list[str]:
    """Return all current OOS unit IDs, oldest-first."""
    with _conn() as c:
        rows = c.execute(
            "SELECT unit FROM oos_units ORDER BY added_at"
        ).fetchall()
    return [r["unit"] for r in rows]


def add_oos_unit(unit: str) -> None:
    """Add a unit to OOS. Silently ignores duplicates."""
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO oos_units (unit, added_at) VALUES (?,?)",
            (unit.strip(), _now()),
        )


def remove_oos_unit(unit: str) -> None:
    """Remove a single unit from OOS once the MST confirms it is back in service.
    Does NOT log the RTS event — callers should call log_rts_event() separately
    so the shift context can be attached.
    """
    with _conn() as c:
        c.execute("DELETE FROM oos_units WHERE unit=?", (unit.strip(),))


def log_rts_event(shift_id: int | None, unit: str) -> None:
    """Record that a unit was returned to service during a specific shift."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        c.execute(
            "INSERT INTO rts_log (shift_id, unit, removed_at) VALUES (?,?,?)",
            (shift_id, unit.strip(), now),
        )


def get_rts_events(shift_id: int) -> list[str]:
    """Return list of unit IDs returned to service during the given shift."""
    with _conn() as c:
        rows = c.execute(
            "SELECT unit FROM rts_log WHERE shift_id=? ORDER BY removed_at",
            (shift_id,),
        ).fetchall()
    return [r["unit"] for r in rows]


def delete_rts_event(shift_id: int, unit: str) -> None:
    """Remove a single unit from the RTS log for the given shift.
    Deletes only the most-recent matching entry so duplicates stay clean.
    """
    with _conn() as c:
        c.execute(
            """
            DELETE FROM rts_log WHERE id = (
                SELECT id FROM rts_log
                WHERE shift_id=? AND unit=?
                ORDER BY removed_at DESC
                LIMIT 1
            )
            """,
            (shift_id, unit.strip()),
        )


def remove_rts_from_oos(rts_units: list[str]) -> None:
    """Auto-clear any units from OOS that also appear in the RTS list."""
    if not rts_units:
        return
    with _conn() as c:
        c.executemany(
            "DELETE FROM oos_units WHERE unit=?",
            [(u.strip(),) for u in rts_units if u.strip()],
        )


# ---------------------------------------------------------------------------
# OT Codes
# ---------------------------------------------------------------------------

def create_ot_code(code: str, assoc_num: str, assoc_name: str,
                   assigned_shift: str, requested_shift: str) -> None:
    """Store a new manager-approval OT code valid for 4 hours."""
    from datetime import timedelta
    now    = datetime.now()
    expiry = now + timedelta(hours=4)
    with _conn() as c:
        c.execute(
            "INSERT OR IGNORE INTO ot_codes "
            "(code,assoc_num,assoc_name,assigned_shift,requested_shift,created_at,expires_at,used)"
            " VALUES (?,?,?,?,?,?,?,0)",
            (code, assoc_num.strip(), assoc_name,
             assigned_shift, requested_shift,
             now.strftime("%Y-%m-%d %H:%M:%S"),
             expiry.strftime("%Y-%m-%d %H:%M:%S")),
        )


def get_pending_ot_codes() -> list[dict]:
    """Return all unused, non-expired OT codes for the manager panel."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM ot_codes WHERE used=0 AND expires_at > ? ORDER BY created_at DESC",
            (now,),
        ).fetchall()
    return [dict(r) for r in rows]


def validate_ot_code(code: str, assoc_num: str, requested_shift: str) -> bool:
    """Return True if the code is valid, unused, unexpired, and matches the assoc + shift."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        row = c.execute(
            "SELECT id FROM ot_codes WHERE code=? AND assoc_num=? "
            "AND requested_shift=? AND used=0 AND expires_at > ?",
            (code.strip().upper(), assoc_num.strip(), requested_shift, now),
        ).fetchone()
    return row is not None


def consume_ot_code(code: str) -> None:
    """Mark a code as used so it cannot be reused."""
    with _conn() as c:
        c.execute("UPDATE ot_codes SET used=1 WHERE code=?", (code.strip().upper(),))


def purge_expired_ot_codes() -> None:
    """Remove used or expired codes — called at startup."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _conn() as c:
        c.execute("DELETE FROM ot_codes WHERE used=1 OR expires_at <= ?", (now,))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Settings  (key-value store for admin-configurable options)
# ---------------------------------------------------------------------------

def get_setting(key: str, default: str = "") -> str:
    with _conn() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO settings (key, value) VALUES (?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def init_settings(**defaults: str) -> None:
    """Populate settings with defaults only if the key doesn't already exist."""
    with _conn() as c:
        for key, value in defaults.items():
            c.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?,?)",
                (key, value),
            )


# ---------------------------------------------------------------------------
# Tech done  (tracks which techs have signed off for the shift)
# ---------------------------------------------------------------------------

def toggle_tech_done(shift_id: int, tech_name: str) -> bool:
    """Toggle done state. Returns True if now done, False if now undone."""
    with _conn() as c:
        existing = c.execute(
            "SELECT id FROM tech_done WHERE shift_id=? AND tech_name=?",
            (shift_id, tech_name),
        ).fetchone()
        if existing:
            c.execute("DELETE FROM tech_done WHERE shift_id=? AND tech_name=?",
                      (shift_id, tech_name))
            return False
        else:
            c.execute(
                "INSERT INTO tech_done (shift_id, tech_name, marked_at) VALUES (?,?,?)",
                (shift_id, tech_name, _now()),
            )
            return True


def get_done_techs(shift_id: int) -> set[str]:
    """Return set of tech_names who have marked themselves done this shift."""
    with _conn() as c:
        rows = c.execute(
            "SELECT tech_name FROM tech_done WHERE shift_id=?", (shift_id,)
        ).fetchall()
    return {r["tech_name"] for r in rows}


# ---------------------------------------------------------------------------
# Associates  (DB-backed roster per facility; seeded from Excel on first run)
# ---------------------------------------------------------------------------

def _make_display_name(full: str) -> str:
    """'ALEXANDER CARR' → 'Alexander C'"""
    parts = full.strip().split()
    if not parts:
        return full.title()
    first = parts[0].capitalize()
    last  = parts[-1][0].upper() if len(parts) > 1 else ""
    return f"{first} {last}".strip()


def seed_associates(roster: dict) -> None:
    """Populate associates table from an in-memory roster dict (Excel import).
    Only runs if the table is currently empty (preserves manual edits).
    """
    with _conn() as c:
        count = c.execute("SELECT COUNT(*) FROM associates").fetchone()[0]
        if count > 0:
            return  # already seeded — don't overwrite edits
        rows = [
            (
                v["assoc_num"],
                v.get("full_name", v["display_name"]),
                v["display_name"],
                v.get("default_id", ""),
                v.get("shift", ""),
                v.get("status", "Active"),
                1 if v.get("is_manager") else 0,
            )
            for v in roster.values()
        ]
        c.executemany(
            "INSERT OR IGNORE INTO associates "
            "(assoc_num, full_name, display_name, default_id, shift, status, is_manager) "
            "VALUES (?,?,?,?,?,?,?)",
            rows,
        )


def get_associates() -> list[dict]:
    """Return all associates sorted by name, managers last."""
    with _conn() as c:
        rows = c.execute(
            "SELECT assoc_num, full_name, display_name, default_id, shift, status, is_manager, is_mst "
            "FROM associates ORDER BY is_manager ASC, full_name ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_associate(assoc_num: str) -> dict | None:
    """Return a single associate by associate number, or None if not found."""
    with _conn() as c:
        row = c.execute(
            "SELECT assoc_num, full_name, display_name, default_id, shift, status, is_manager, is_mst "
            "FROM associates WHERE assoc_num=?",
            (assoc_num,),
        ).fetchone()
    return dict(row) if row else None


def get_associate(assoc_num: str) -> dict | None:
    with _conn() as c:
        row = c.execute(
            "SELECT * FROM associates WHERE assoc_num=?", (assoc_num,)
        ).fetchone()
    return dict(row) if row else None


def add_associate(assoc_num: str, full_name: str,
                  shift: str = "", default_id: str = "",
                  is_mst: bool = False) -> None:
    display = _make_display_name(full_name)
    with _conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO associates "
            "(assoc_num, full_name, display_name, default_id, shift, status, is_manager, is_mst) "
            "VALUES (?,?,?,?,?,'Active',0,?)",
            (assoc_num.strip(), full_name.strip().upper(), display,
             default_id.strip(), shift.strip(), 1 if is_mst else 0),
        )


def set_associate_mst(assoc_num: str, is_mst: bool) -> None:
    """Toggle MST status for an associate. MSTs can log into the lead dashboard."""
    with _conn() as c:
        c.execute("UPDATE associates SET is_mst=? WHERE assoc_num=?",
                  (1 if is_mst else 0, assoc_num))


def delete_associate(assoc_num: str) -> None:
    with _conn() as c:
        c.execute("DELETE FROM associates WHERE assoc_num=? AND is_manager=0", (assoc_num,))

def associates_to_roster() -> dict:
    """Convert associates table back to the in-memory roster dict format."""
    from associates import MANAGER_CODE, _MANAGER_ENTRY
    roster: dict = {}

    for a in get_associates():
        roster[a["assoc_num"]] = {
            "display_name": a["display_name"],
            "full_name":    a["full_name"].title(),
            "assoc_num":    a["assoc_num"],
            "default_id":   a["default_id"],
            "shift":        a["shift"],
            "status":       a["status"],
            "is_manager":   bool(a["is_manager"]),
            "is_mst":       bool(a.get("is_mst", 0)),
        }
    roster[MANAGER_CODE] = _MANAGER_ENTRY
    return roster


def force_reseed_associates(roster: dict) -> None:
    """Upsert all non-manager associates from a fresh roster dict.
    - New records: takes is_mst from the loaded data (e.g. Job Title == MST from Drax).
    - Existing records: preserves the DB's is_mst so admin toggles aren't overwritten.
    """
    rows = [
        (
            v["assoc_num"],
            v.get("full_name", v["display_name"]).upper(),
            v["display_name"],
            v.get("default_id", ""),
            v.get("shift", ""),
            v.get("status", "Active"),
            0,  # is_manager
            1 if v.get("is_mst") else 0,  # is_mst from Drax (only applied on INSERT)
        )
        for v in roster.values()
        if not v.get("is_manager")
    ]
    with _conn() as c:
        c.executemany(
            """
            INSERT INTO associates
                (assoc_num, full_name, display_name, default_id, shift, status, is_manager, is_mst)
            VALUES (?,?,?,?,?,?,?,?)
            ON CONFLICT(assoc_num) DO UPDATE SET
                full_name    = excluded.full_name,
                display_name = excluded.display_name,
                default_id   = excluded.default_id,
                shift        = excluded.shift,
                status       = excluded.status,
                -- Promote to MST if Drax says so; never auto-demote (admin toggle wins)
                is_mst       = MAX(is_mst, excluded.is_mst)
            """,
            rows,
        )

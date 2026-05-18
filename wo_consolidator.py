"""WO Consolidator — merge multiple entries sharing the same Work Order number.

When multiple technicians upload timecards referencing the same WO, each gets
its own row in `entries`. This module merges them into a single ConsolidatedWO
block for display and reporting — WITHOUT touching raw data.

Rules
-----
- Only non-excluded entries with a non-empty wo_number are grouped.
- Entries with no wo_number pass through untouched (passthrough list).
- Groups are sorted by created_at ASC before merging (oldest first).
- Conflict detection: "resolved" KW + "ongoing" KW both present → conflicting=True.
- Conflict resolution: prefer summary from the LATEST entry's text.
- asset_id: taken from the FIRST entry (most authoritative Cetaris import).
- Manual entries (source != 'cmms') with a WO number contribute their
  issue text as a remark when raw_remarks is empty.
- Empty all_remarks → fallback summary string so report never has blank bullet.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime

from summarizer import _smart_concat

# ── Keyword sets for conflict detection ──────────────────────────────────────

_RESOLVED_KW = frozenset({
    "fixed", "repaired", "replaced", "resolved", "returned to service",
    "rts", "completed", "cleared", "restored", "corrected",
})

_ONGOING_KW = frozenset({
    "pending", "monitoring", "investigating", "parts ordered",
    "waiting", "on order", "unresolved", "work in progress", "wip",
    "parts on order", "deferred", "still", "open",
})

_PRIORITY_RANK = {"critical": 3, "high": 2, "monitor": 1, "low": 0}

_WO_PREFIX_RE = re.compile(r"^WO\s*#?\s*\d+\s*[\-—–:]\s*", re.IGNORECASE)


# ── Data structures ───────────────────────────────────────────────────────────

@dataclass
class ConsolidatedWO:
    """One merged block representing all entries for a single Work Order."""
    wo_number: str
    asset_id: str           # unit field from the FIRST entry (most authoritative)
    techs: list[str]        # unique tech names, first-seen order
    all_remarks: list[str]  # deduped union of all raw_remarks across entries
    summary: str            # _smart_concat(all_remarks) or LLM summary
    priority: str           # highest priority across the group
    first_logged: str       # earliest created_at (ISO string)
    last_logged: str        # latest created_at (ISO string)
    raw_entry_ids: list[int]
    entry_count: int
    conflicting: bool = False   # resolved KW AND ongoing KW both found


@dataclass
class ConsolidationResult:
    """Full output of consolidate_entries()."""
    consolidated: list[ConsolidatedWO]  # one per WO, sorted by first_logged
    passthrough: list[dict]             # entries with empty wo_number (untouched)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _parse_dt(ts: str) -> datetime:
    """Parse ISO datetime string; fall back to epoch on bad data."""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            pass
    return datetime(1970, 1, 1)


def _highest_priority(priorities: list[str]) -> str:
    return max(priorities, key=lambda p: _PRIORITY_RANK.get(p, 0), default="monitor")


def _dedup_remarks(raw: list[str]) -> list[str]:
    """Return unique remarks, preserving order, case-insensitive dedup."""
    seen: set[str] = set()
    out: list[str] = []
    for r in raw:
        norm = r.strip().lower()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(r.strip())
    return out


def _detect_conflict(remarks: list[str]) -> bool:
    """True if both a resolved and an ongoing keyword appear across remarks."""
    blob = " ".join(remarks).lower()
    has_resolved = any(kw in blob for kw in _RESOLVED_KW)
    has_ongoing  = any(kw in blob for kw in _ONGOING_KW)
    return has_resolved and has_ongoing


def _collect_remarks(entry: dict) -> list[str]:
    """Pull remarks out of an entry, falling back to issue text for manual entries."""
    raw: list[str] = []
    try:
        raw = json.loads(entry.get("raw_remarks") or "[]")
    except (json.JSONDecodeError, TypeError):
        pass

    if not raw:
        # Manual entry or CMMS entry with no remarks — use issue text
        issue = entry.get("issue", "").strip()
        if issue:
            # Strip the "WO #XXXX — " prefix that upsert_cmms_entry prepends
            issue = _WO_PREFIX_RE.sub("", issue).strip().rstrip(".")
        if issue:
            raw = [issue]

    return raw


def _build_wo(wo_number: str, group: list[dict]) -> ConsolidatedWO:
    """Build one ConsolidatedWO from a chronologically-sorted group of entries."""
    first = group[0]
    last  = group[-1]

    # Unique tech names in first-seen order
    seen_techs: set[str] = set()
    techs: list[str] = []
    for e in group:
        name = (e.get("tech_name") or "").strip()
        if name and name not in seen_techs:
            seen_techs.add(name)
            techs.append(name)

    # Collect all remarks across every entry, then dedup
    all_raw: list[str] = []
    for e in group:
        all_raw.extend(_collect_remarks(e))
    all_remarks = _dedup_remarks(all_raw)

    # Summary: smart-concat the deduped remarks; guarantee non-empty string
    if all_remarks:
        summary = _smart_concat(all_remarks)
    else:
        tech_str = ", ".join(techs) if techs else "unknown tech"
        summary = f"WO #{wo_number} worked on by {tech_str}. No remarks recorded."

    return ConsolidatedWO(
        wo_number    = wo_number,
        asset_id     = (first.get("unit") or "").strip(),   # first entry = most authoritative
        techs        = techs,
        all_remarks  = all_remarks,
        summary      = summary,
        priority     = _highest_priority([e.get("priority", "monitor") for e in group]),
        first_logged = first.get("created_at", ""),
        last_logged  = last.get("created_at", ""),
        raw_entry_ids= [e["id"] for e in group],
        entry_count  = len(group),
        conflicting  = _detect_conflict(all_remarks),
    )


# ── Public API ────────────────────────────────────────────────────────────────

def consolidate_entries(entries: list[dict]) -> ConsolidationResult:
    """Group entries by WO number and merge each group into one ConsolidatedWO.

    Args:
        entries: Raw entry dicts from db.get_entries() — already ordered by
                 created_at ASC, but we re-sort inside each group for safety.

    Returns:
        ConsolidationResult with:
          .consolidated  — one ConsolidatedWO per unique WO, sorted by first_logged
          .passthrough   — non-excluded entries with no wo_number (unchanged)
    """
    # Step 1 — pre-filter excluded entries (they must never appear in reports)
    active = [e for e in entries if not e.get("excluded")]

    # Step 2 — split: has WO vs no WO
    wo_groups: dict[str, list[dict]] = {}
    passthrough: list[dict] = []

    for e in active:
        wo = (e.get("wo_number") or "").strip()
        if wo:
            wo_groups.setdefault(wo, []).append(e)
        else:
            passthrough.append(e)

    # Step 3 — sort each group chronologically and build ConsolidatedWO
    consolidated: list[ConsolidatedWO] = []
    for wo_number, group in wo_groups.items():
        group.sort(key=lambda e: _parse_dt(e.get("created_at", "")))
        consolidated.append(_build_wo(wo_number, group))

    # Step 4 — sort consolidated blocks by first_logged (chronological report order)
    consolidated.sort(key=lambda w: _parse_dt(w.first_logged))

    return ConsolidationResult(
        consolidated = consolidated,
        passthrough  = passthrough,
    )


def consolidated_to_happenings(
    result: ConsolidationResult,
    wo_prefix_re: re.Pattern | None = None,
) -> list[dict]:
    """Convert a ConsolidationResult into a list of plain dicts suitable for
    building RewrittenHappening objects in both the lead_generate and
    shift_end report paths.

    Each dict has keys: title, paragraph, wo_number, tech_name, conflicting
    """
    out: list[dict] = []

    for wo in result.consolidated:
        tech_str = ", ".join(wo.techs) if wo.techs else ""
        conflict_note = " [⚠ Conflicting notes — verify status]" if wo.conflicting else ""
        out.append({
            "title":      wo.asset_id or f"WO #{wo.wo_number}",
            "paragraph":  wo.summary + conflict_note,
            "wo_number":  wo.wo_number,
            "tech_name":  tech_str,
            "conflicting": wo.conflicting,
        })

    for e in result.passthrough:
        issue = (e.get("issue") or "").strip()
        if wo_prefix_re:
            issue = wo_prefix_re.sub("", issue).strip().rstrip(".")
        out.append({
            "title":      (e.get("unit") or "").strip(),
            "paragraph":  issue,
            "wo_number":  "",
            "tech_name":  e.get("tech_name", ""),
            "conflicting": False,
        })

    return out

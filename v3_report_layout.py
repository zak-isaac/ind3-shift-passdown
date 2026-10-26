"""One authoritative V3 Shift Happenings layout for preview, reports and exports.

A work order contributes one asset/cause heading and one technician-remarks
bullet. Multi-technician remarks are combined verbatim, separated by '; ',
after safe dictionary corrections. Never synthesize a repair or outcome.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict

from report_models import RewrittenHappening
from v3_proofread import proofread

_WO_PREFIX = re.compile(r"^WO\s*#?\s*\d+\s*[-\u2013\u2014:]\s*", re.IGNORECASE)


def _raw_remarks(entry: dict) -> list[str]:
    text = entry.get("raw_remarks") or "[]"
    try:
        value = json.loads(text)
        if isinstance(value, list):
            parts = [str(v).strip() for v in value if str(v).strip()]
            if parts:
                return parts
    except (ValueError, TypeError):
        pass
    # Imported Cetaris entries with blank remarks must not re-use the cause
    # as a made-up technician remark. Only manual entries use action fallback.
    if entry.get("source") == "cmms":
        return []
    action = (entry.get("action") or "").strip()
    return [action] if action else []


def _remark_key(text: str) -> str:
    """Ignore only harmless trailing full stops and whitespace when deduping.

    Never use fuzzy/string-similarity dedup: "left" vs "right", different
    measurements, different component IDs and RTS vs OOS must all survive.
    """
    return re.sub(r"\.+$", "", re.sub(r"\s+", " ", text).strip()).casefold()


def build_shift_happenings(entries: list[dict], meta_by_id: dict[int, dict]
                           | None = None, approved_remarks: dict[str, str] | None = None) -> list[RewrittenHappening]:
    """Build one report happening per unique WO (or individual manual entry)."""
    meta_by_id = meta_by_id or {}
    approved_remarks = approved_remarks or {}
    groups: OrderedDict[str, dict] = OrderedDict()
    for entry in entries:
        if entry.get("excluded"):
            continue
        wo = str(entry.get("wo_number") or "").strip()
        key = f"wo:{wo}" if wo else f"manual:{entry['id']}"
        meta = meta_by_id.get(entry["id"], {})
        raw_cause = (meta.get("cause_text") or meta.get("reported_issue") or
                     _WO_PREFIX.sub("", str(entry.get("issue") or ""))).strip()
        cause = proofread(raw_cause)
        asset = str(entry.get("unit") or "").strip() or (f"WO #{wo}" if wo else "Unassigned asset")
        # An MST-approved edit wins over the originally imported cause,
        # regardless of which collaborating technician appears first.
        cause_rank = (
            3 if meta.get("mst_edited") and meta.get("cause_text")
            else 2 if meta.get("cause_text")
            else 1 if meta.get("reported_issue")
            else 0
        )
        group = groups.setdefault(key, {
            "asset": asset, "cause": cause, "cause_rank": cause_rank,
            "wo_number": wo, "remarks": [], "seen": set(), "mst_edited": False,
        })
        if cause and (not group["cause"] or cause_rank > group["cause_rank"]):
            group["cause"] = cause
            group["cause_rank"] = cause_rank
        if meta.get("mst_edited"):
            group["mst_edited"] = True
        for raw in _raw_remarks(entry):
            corrected = proofread(raw.strip())
            token = _remark_key(corrected)
            if token and token not in group["seen"]:
                group["seen"].add(token)
                group["remarks"].append(corrected)
    return [
        RewrittenHappening(
            title=g["asset"],
            cause=g["cause"],
            paragraph=proofread(
                (None if g["mst_edited"] else approved_remarks.get(g["wo_number"]))
                or ("; ".join(g["remarks"]) if g["remarks"] else "No technician remarks recorded.")
            ),
            wo_number=g["wo_number"],
        )
        for g in groups.values()
    ]


def heading(h: RewrittenHappening) -> str:
    """Asset ID: Cause, with no fabricated cause if source was blank."""
    asset = (h.title or "").strip()
    cause = (h.cause or "").strip()
    return f"{asset}: {cause}" if cause else asset


def remark(h: RewrittenHappening) -> str:
    """One displayable technician-remarks bullet per WO."""
    written = (h.paragraph or "").strip()
    if written:
        return written
    if h.sub_points:  # Legacy saved reports remain readable.
        return "; ".join(str(item[1]).strip() for item in h.sub_points if str(item[1]).strip())
    return "No technician remarks recorded."

"""Shift happening rewriter – pure template-based, zero dependencies.

Classifies each raw note (repair / informational / general) and wraps it
in a polished professional paragraph. No LLM, no API keys, no network.
"""
from __future__ import annotations
from report_models import HappeningInput, RewrittenHappening

_REPAIR_KW = frozenset({
    "fixed", "repaired", "replaced", "adjusted", "reset", "cleared",
    "corrected", "resolved", "rts", "returned to service", "restored",
    "calibrated", "tightened", "lubricated", "swapped", "installed",
    "unlocked", "unlock", "released",
})
_INFO_KW = frozenset({
    "confirmed", "reviewed", "clarified", "explained", "verified",
    "inspected", "assessed", "evaluated", "checked", "no fault",
    "no issue", "manager asked", "question", "asked if",
})


def _classify(note: str) -> str:
    lowered = note.lower()
    if any(kw in lowered for kw in _REPAIR_KW):
        return "repair"
    if any(kw in lowered for kw in _INFO_KW):
        return "info"
    return "general"


def rewrite_happening(item: HappeningInput) -> RewrittenHappening:
    """Transform a raw note into a polished paragraph."""
    note = item.raw_note.strip().rstrip(".")
    kind = _classify(note)

    if kind == "repair":
        para = (
            f"A maintenance condition was identified involving {item.title}. "
            f"{note}. "
            f"Corrective action was completed and the equipment was verified "
            f"for proper operation prior to being returned to service."
        )
    elif kind == "info":
        para = (
            f"An operational review was conducted regarding {item.title}. "
            f"{note}. "
            f"No mechanical, electrical, or control fault was identified "
            f"during this review."
        )
    else:
        para = (
            f"Attention was directed to {item.title} during this shift. "
            f"{note}. "
            f"The matter was assessed and addressed by the maintenance team."
        )

    return RewrittenHappening(title=item.title, paragraph=para)


def rewrite_all(items: list[HappeningInput]) -> list[RewrittenHappening]:
    """Rewrite all happenings synchronously."""
    return [rewrite_happening(i) for i in items]

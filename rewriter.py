"""Shift happening rewriter.

Primary path : local Phi-4 Mini via ai_rewriter (Ollama).
Fallback path: template-based classification (zero dependencies).

The AI step is completely transparent to callers — this module's
public API is unchanged.
"""
from __future__ import annotations
from report_models import HappeningInput, RewrittenHappening
import ai_rewriter

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
    """Transform a raw note into a polished paragraph.

    Tries the local AI model first. Falls back to template-based
    rewriting when Ollama is unavailable or the request fails.
    """
    note = item.raw_note.strip().rstrip(".")

    # ── Primary: AI rewrite ───────────────────────────────────────────────
    ai_para = ai_rewriter.rewrite(f"{item.title} {note}")
    if ai_para and ai_para.strip() != f"{item.title} {note}".strip():
        return RewrittenHappening(title=item.title, paragraph=ai_para)

    # ── Fallback: template-based classification ───────────────────────────
    kind = _classify(note)
    if kind == "repair":
        para = (
            f"A reliability condition was identified involving {item.title}. "
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
            f"The matter was assessed and addressed by the reliability team."
        )

    return RewrittenHappening(title=item.title, paragraph=para)


def rewrite_all(items: list[HappeningInput]) -> list[RewrittenHappening]:
    """Rewrite all happenings synchronously."""
    return [rewrite_happening(i) for i in items]

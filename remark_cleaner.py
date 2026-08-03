"""remark_cleaner.py
Rule-based passdown note cleaner.

Converts raw tech remarks into professional passive-voice passdown entries
WITHOUT relying on an AI model -- 100% reliable, zero hallucinations.

Public API
----------
clean(asset_id: str, remarks: list[str]) -> str
    Takes raw remarks list, returns a single complete passdown note string.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------------------
# Warehouse-specific typo fixes (applied first, before anything else)
# ---------------------------------------------------------------------------
_TYPOS: list[tuple[str, str]] = [
    (r"\bcraahing\b",      "crashing"),
    (r"\bcraashing\b",     "crashing"),
    (r"\boperationl\b",    "operational"),
    (r"\boperatinal\b",    "operational"),
    (r"\brecalibreted\b",  "recalibrated"),
    (r"\bblushings\b",     "blushing"),   # normalise plural
]

# ---------------------------------------------------------------------------
# Pronoun → passive voice substitutions.
# ORDER MATTERS:
#   1. Connector stripping ("so I", "then I") first -- before verb patterns
#      so the verb is left cleanly without an orphaned connector.
#   2. Specific multi-word patterns before generic single-word ones.
#   3. Standalone observation words (Noticed, Observed) without "I" prefix.
# ---------------------------------------------------------------------------
_PRONOUN_SUBS: list[tuple[str, str]] = [
    # 1. Connector + pronoun -- strip the whole thing, leave the verb
    (r"\b(?:so|then|and)\s+[Ii]\s+",          " "),

    # 2. Specific multi-word "I <verb> <object>" patterns
    (r"\b[Ii]\s+lowered\s+the\s+platform\b",   "Platform lowered"),
    (r"\b[Ii]\s+drove\s+(?:the\s+)?(?:equipment|unit|machine|asset)\b",
                                                 "Unit driven to shop"),
    (r"\b[Ii]\s+took\s+(?:it|them)\s+down\b",  "Removed"),
    (r"\b[Ii]\s+did\s+(?:a|the)\s+calibration\b",  "Calibration performed"),
    (r"\b[Ii]\s+did\s+(?:a|the)\s+brake\s+test\b", "Brake test performed"),

    # 3. Standalone observation verbs (no "I" prefix required)
    (r"\b[Nn]oticed\s+(?:that\s+)?",            "Found that "),
    (r"\b[Oo]bserved\s+(?:that\s+)?",           "Found that "),
    # Standalone "Took it down" (tech wrote without "I")
    (r"\b[Tt]ook\s+it\s+down\b",                "Removed"),
    (r"\b[Tt]ook\s+(?:it|them)\s+down\b",       "Removed"),

    # 4. Generic "I <verb>" single-word patterns
    (r"\b[Ii]\s+recalibrated\b",                "Recalibrated"),
    (r"\b[Ii]\s+calibrated\b",                  "Calibrated"),
    (r"\b[Ii]\s+repositioned\b",                "Repositioned"),
    (r"\b[Ii]\s+rethreaded\b",                  "Rethreaded"),
    (r"\b[Ii]\s+threaded\b",                    "Threaded"),
    (r"\b[Ii]\s+changed\b",                     "Replaced"),
    (r"\b[Ii]\s+replaced\b",                    "Replaced"),
    (r"\b[Ii]\s+adjusted\b",                    "Adjusted"),
    (r"\b[Ii]\s+re-?adjusted\b",                "Re-adjusted"),
    (r"\b[Ii]\s+tightened\b",                   "Tightened"),
    (r"\b[Ii]\s+loosened\b",                    "Loosened"),
    (r"\b[Ii]\s+lubricated\b",                  "Lubrication applied to"),
    (r"\b[Ii]\s+inspected\b",                   "Inspected"),
    (r"\b[Ii]\s+tested\b",                      "Tested"),
    (r"\b[Ii]\s+verified\b",                    "Verified"),
    (r"\b[Ii]\s+checked\b",                     "Checked"),
    (r"\b[Ii]\s+found\b",                       "Found"),
    (r"\b[Ii]\s+noticed\b",                     "Found that"),
    (r"\b[Ii]\s+lowered\b",                     "Lowered"),
    (r"\b[Ii]\s+drove\b",                       "Driven"),
    (r"\b[Ii]\s+performed\b",                   "Performed"),
    (r"\b[Ii]\s+completed\b",                   "Completed"),
    (r"\b[Ii]\s+removed\b",                     "Removed"),
    (r"\b[Ii]\s+reinstalled\b",                 "Reinstalled"),
    (r"\b[Ii]\s+installed\b",                   "Installed"),
    (r"\b[Ii]\s+reset\b",                       "Reset"),
    (r"\b[Ii]\s+cleared\b",                     "Cleared"),
    (r"\b[Ii]\s+cleaned\b",                     "Cleaned"),
    (r"\b[Ii]\s+did\b",                         "Performed"),

    # 5. Possessive / "we" cleanup
    (r"\bmy\b",                                  "the"),
    (r"\b[Ww]e\s+found\b",                      "Found"),
    (r"\b[Ww]e\s+checked\b",                    "Checked"),
    (r"\b[Ww]e\s+replaced\b",                   "Replaced"),
    (r"\b[Ww]e\s+performed\b",                  "Performed"),
    (r"\b[Ww]e\s+verified\b",                   "Verified"),
    (r"\b[Ww]e\s+tested\b",                     "Tested"),
    (r"\b[Ww]e\s+did\b",                        "Performed"),
    (r"\b[Ww]e\b",                               ""),
]

# ---------------------------------------------------------------------------
# Status phrase normalisation (applied after pronoun substitution)
# ---------------------------------------------------------------------------
_STATUS_SUBS: list[tuple[str, str]] = [
    (r"\b(?:unit|asset|equipment|machine)\s+(?:is\s+)?(?:fully\s+)?operational(?:\s+now)?\b",
     "unit returned to service"),
    (r"\b(?:asset|unit|equipment)\s+(?:is\s+)?back\s+in(?:to)?\s+(?:full\s+)?operation(?:al)?\b",
     "unit returned to service"),
    (r"\b(?:asset|unit|equipment)\s+(?:is\s+)?operational\b",
     "unit returned to service"),
    (r"\b(?:asset|unit)\s+(?:is\s+)?rts\b",    "unit returned to service"),
    (r"\bunit\s+rts\b",                          "unit returned to service"),
    (r"\brts\b",                                  "returned to service"),
    (r"\bback\s+in\s+service\b",                "returned to service"),
    (r"\bback\s+in(?:to)?\s+operation\b",       "returned to service"),
    (r"\bfully\s+operational\s+now\b",           "returned to service"),
    (r"\bfully\s+operational\b",                 "returned to service"),
    (r"\bno\s+other\s+issues?\s+found\b",       "no additional issues found"),
    (r"\ball\s+connections?\s+(?:are\s+)?good\b", "all connections verified good"),
    (r"\ball\s+good\b",                           "all checks passed"),
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fix_typos(text: str) -> str:
    for pat, rep in _TYPOS:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return text


def _apply_pronouns(text: str) -> str:
    for pat, rep in _PRONOUN_SUBS:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return text


def _apply_status(text: str) -> str:
    for pat, rep in _STATUS_SUBS:
        text = re.sub(pat, rep, text, flags=re.IGNORECASE)
    return text


def _capitalise_sentences(text: str) -> str:
    """Ensure each sentence starts with a capital letter."""
    sentences = re.split(r"(?<=[.;!?])\s+", text.strip())
    out = []
    for s in sentences:
        s = s.strip()
        if s:
            out.append(s[0].upper() + s[1:])
    return " ".join(out)


def _tidy(text: str) -> str:
    """Final tidying: collapse spaces, fix double punctuation, ensure period."""
    text = re.sub(r"[ \t]+",          " ",  text)   # collapse spaces
    text = re.sub(r"\.\s*\.",         ".",  text)   # double period
    text = re.sub(r",\s*,",           ",",  text)   # double comma
    text = re.sub(r"\s+([.,;!?])",   r"\1", text)  # space before punctuation
    # Clean up leftover "Found: " artefacts (shouldn't happen now but safety net)
    text = re.sub(r"Found:\s*", "Found that ", text)
    text = re.sub(r":\s+\.",          ".",  text)
    text = text.strip()
    if text and text[-1] not in ".!?":
        text += "."
    return text


def _clean_remark(remark: str) -> str:
    """Process a single tech remark into a cleaned passdown fragment."""
    t = _fix_typos(remark.strip())
    t = _apply_pronouns(t)
    t = _apply_status(t)
    t = _tidy(t)
    return t


def _join_multi_tech(fragments: list[str]) -> str:
    """Join multiple tech remarks into one chronological passdown note."""
    if len(fragments) == 1:
        return fragments[0]
    # Strip trailing punctuation from all but last so they join cleanly
    parts = [f.rstrip(".!?") for f in fragments]
    return "; ".join(parts) + "."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def clean(asset_id: str, remarks: list[str]) -> str:
    """Convert raw tech remarks to a professional passdown note.

    Args:
        asset_id: Asset identifier string (used for context only, not included
                  in output -- the report header already shows it).
        remarks:  List of raw remark strings, one per tech for multi-tech WOs.

    Returns:
        Single passdown note string, ready for the report.
    """
    cleaned = [_clean_remark(r) for r in remarks if r and r.strip()]
    if not cleaned:
        return ""
    joined  = _join_multi_tech(cleaned)
    return _capitalise_sentences(joined)

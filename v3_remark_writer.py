"""Conservative, no-LLM maintenance remark writer prototype.

Emits a rewritten paragraph only for explicitly recognized, tested structures.
All other cases preserve nonduplicate notes and require MST review.
This is NOT yet connected to production report generation.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from v3_proofread import proofread


@dataclass(frozen=True)
class Draft:
    text: str
    mode: str
    requires_review: bool
    reason: str = ""


def _unique(notes: list[str]) -> list[str]:
    result, seen = [], set()
    for note in notes:
        value = proofread(str(note or "").strip())
        if not value:
            continue
        key = re.sub(r"[.! ]+$", "", re.sub(r"\s+", " ", value)).casefold()
        if key not in seen:
            result.append(value)
            seen.add(key)
    return result


def _preserved(notes: list[str], reason: str) -> Draft:
    return Draft("; ".join(notes) if notes else "No technician remarks recorded.",
                 "preserved", True, reason)


def rewrite_wo(remarks: list[str]) -> Draft:
    """Rewrite only known patterns, regardless of technician count."""
    notes = _unique(remarks)
    if not notes:
        return _preserved(notes, "No technician evidence")
    if len(notes) == 1:
        return Draft(notes[0], "single", False)

    paper_forms = {"paper adjusted", "paper adjustment"}
    if all(n.casefold().rstrip(". ") in paper_forms for n in notes):
        return Draft("Adjusted the paper.", "rule:paper-adjustment", False)

    wheels = [n for n in notes if re.fullmatch(
        r"changed both\s+(?:right and left|left and right) load wheels\.?", n,
        flags=re.I)]
    metrics = [n for n in notes if "blue light" in n.casefold() and
               "braking" in n.casefold() and "plugging" in n.casefold()]
    if len(notes) == 2 and len(wheels) == 1 and len(metrics) == 1:
        m = metrics[0]
        found = []
        for label in ("blue light", "braking", "plugging"):
            match = re.search(r"\b"+label+r"\s*:?\s*(\d+(?:\.\d+)?)\s*(ft|feet)\b",
                              m, flags=re.I)
            if match is None:
                break
            found.append(f"{match.group(1)} {match.group(2).lower()}")
        if len(found) == 3:
            return Draft(
                "Replaced both the left and right load wheels. "
                f"Recorded the blue light at {found[0]}, braking at {found[1]}, "
                f"and plugging at {found[2]}.",
                "rule:wheels-and-measurements", False)

    statuses = set(x.upper() for x in re.findall(
        r"\b(?:RTS|OOS)\b", " ".join(notes), flags=re.I))
    if len(statuses) > 1:
        return _preserved(notes, "Conflicting or asset-specific service statuses")
    return _preserved(notes, "Additional technical actions need narrative review")

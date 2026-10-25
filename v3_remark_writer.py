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



# Exact source-backed equivalence classes. Never infer equivalence using
# arbitrary fuzzy similarity: part numbers, directions and service statuses
# must not be merged into unrelated statements.
_EQUIVALENT_ACTIONS = (
    (re.compile(r"(?:paper\s+adjusted|paper\s+adjustment|adjusted\s+the\s+paper)\.?", re.I),
     "Adjusted the paper."),
    (re.compile(r"(?:motor\s+brush\s+clean\s*up|cleaned\s+(?:the\s+)?motor\s+brush(?:es)?)\.?", re.I),
     "Cleaned the motor brush."),
    (re.compile(r"(?:cleared\s+(?:the\s+)?paper\s+jam|paper\s+jam\s+cleared)\.?", re.I),
     "Cleared the paper jam."),
)


def _equivalent_action(note: str) -> tuple[int, str] | None:
    normalized = re.sub(r"\s+", " ", note).strip()
    for i, (pattern, canonical) in enumerate(_EQUIVALENT_ACTIONS):
        if pattern.fullmatch(normalized):
            return i, canonical
    return None


def _semantic_dedup(notes: list[str]) -> list[str]:
    """Merge only verified stand-alone aliases; keep other notes unmodified."""
    result = []
    observed = set()
    for note in notes:
        alias = _equivalent_action(note)
        if alias is None:
            result.append(note)
        elif alias[0] not in observed:
            result.append(alias[1])
            observed.add(alias[0])
    return result


def rewrite_wo(remarks: list[str]) -> Draft:
    """Rewrite only known patterns, regardless of technician count."""
    notes = _semantic_dedup(_unique(remarks))
    if not notes:
        return _preserved(notes, "No technician evidence")
    if len(notes) == 1:
        return Draft(notes[0], "single", False)

    wheels = [n for n in notes if re.fullmatch(
        r"changed both\s+(?:right and left|left and right) load wheels\.?", n,
        flags=re.I)]
    metrics = [n for n in notes if "blue light" in n.casefold() and
               "braking" in n.casefold() and "plugging" in n.casefold()]
    if len(notes) == 2 and len(wheels) == 1 and len(metrics) == 1:
        m = metrics[0]
        # Only accept precisely three known measurement clauses. If an extra
        # action or observation appears, preserve it rather than dropping it.
        exact_metrics = re.fullmatch(
            r"\s*blue\s+light\s*:?\s*\d+(?:\.\d+)?\s*(?:ft|feet)"
            r"[\s,;]*braking\s*:?\s*\d+(?:\.\d+)?\s*(?:ft|feet)"
            r"[\s,;]*plugging\s*:?\s*\d+(?:\.\d+)?\s*(?:ft|feet)[.!]?\s*",
            m, flags=re.I,
        )
        if not exact_metrics:
            return _preserved(notes, "Additional text beyond verified measurements")
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

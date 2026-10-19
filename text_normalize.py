"""Deterministic, non-AI text normalization for technician remarks.

Companion to spelling_fix.py -- same philosophy (rule-based, reproducible,
never paraphrases), different scope: structural cleanup rather than
per-word spelling. Each function here does exactly one mechanical thing
and is safe to run unconditionally on every remark.
"""
from __future__ import annotations

import re

# Shop-specific shorthand a tech commonly writes that's unambiguous but
# reads better spelled out in a report. Deliberately small and reviewed --
# expand only with terms actually seen in real remarks, not guessed ahead
# of evidence. Case-insensitive whole-word match only (never touches part
# of a longer word or an asset ID like TRKGATE).
ABBREVIATION_EXPANSIONS = {
    "rts": "returned to service",
    "oos": "out of service",
    "pm": "preventive maintenance",
}

_ABBREV_RE = re.compile(
    r"\b(" + "|".join(re.escape(a) for a in ABBREVIATION_EXPANSIONS) + r")\b",
    re.IGNORECASE,
)

# Two identical words in a row (case-insensitive) -- almost always a
# copy-paste or dictation artifact, never intentional in shop remarks.
_DOUBLE_WORD_RE = re.compile(r"\b(\w+)([ \t]+\1)\b", re.IGNORECASE)

# Missing space after a comma/semicolon/colon before the next word.
_MISSING_SPACE_RE = re.compile(r"([,;:])(?=[A-Za-z])")

# Sentence-ending punctuation followed by a lowercase letter -- needs
# capitalizing. Requires a preceding space (or start handled separately
# by callers) to avoid mangling abbreviations/decimals like "e.g." or "3.5".
_SENTENCE_BOUNDARY_RE = re.compile(r"([.!?])(\s+)([a-z])")


def expand_abbreviations(text: str) -> str:
    """Spell out known shop shorthand (rts, oos, pm). Whole-word match
    only; preserves original capitalization style loosely by matching
    case-insensitively but always emitting the standard lowercase
    expansion (these are never proper nouns)."""
    if not text:
        return text

    def _replace(m: re.Match) -> str:
        return ABBREVIATION_EXPANSIONS[m.group(1).lower()]

    return _ABBREV_RE.sub(_replace, text)


def collapse_double_words(text: str) -> str:
    """'checked checked the wire' -> 'checked the wire'."""
    if not text:
        return text
    prev = None
    while prev != text:
        prev = text
        text = _DOUBLE_WORD_RE.sub(r"\1", text)
    return text


def fix_punctuation_spacing(text: str) -> str:
    """'wire,checked' -> 'wire, checked'."""
    if not text:
        return text
    return _MISSING_SPACE_RE.sub(r"\1 ", text)


def fix_sentence_capitalization(text: str) -> str:
    """Capitalize the first letter of every sentence, not just the first
    word of the whole remark -- multi-sentence remarks previously only
    got the very first letter capitalized."""
    if not text:
        return text
    return _SENTENCE_BOUNDARY_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}{m.group(3).upper()}", text)


def normalize(text: str) -> str:
    """Run the full deterministic structural-cleanup pipeline in a fixed,
    safe order. Spelling correction is intentionally NOT included here --
    see spelling_fix.correct_spelling(), applied separately."""
    text = collapse_double_words(text)
    text = fix_punctuation_spacing(text)
    text = expand_abbreviations(text)
    text = fix_sentence_capitalization(text)
    return text

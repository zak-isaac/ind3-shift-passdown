"""Deterministic, LLM-free spelling correction for technician remarks.

2026-09-19: Isaac's final, explicit call after a full night of testing
LLM-based rewriting -- "just fix the punctuation, misspellings, grammar...
don't add new words or edit the sentence to something else... same input
should be same output." That's a description of DETERMINISTIC correction,
which a generative LLM categorically cannot guarantee -- even at low
temperature, tonight's testing showed the exact same raw remark (TSP0008)
producing three different AI outputs across three separate report runs.

This module does ONE narrow thing: fix individually misspelled words,
one word at a time, against a plain English dictionary (pyspellchecker).
It never reorders, removes, or adds words, and never touches word choice
beyond correcting an actual misspelling. It intentionally does NOT attempt
full sentence-level grammar correction (verb tense, subject-verb
agreement, run-on sentences) -- that requires judgment calls about
sentence structure, which is exactly the kind of "creative" latitude that
caused meaning drift and fabrication all night. Spelling + capitalization
+ terminal punctuation (handled elsewhere in ai_rewriter.py's existing
deterministic helpers) covers the overwhelming majority of real issues
seen in real shift data: "colum" -> "column", "intstalled" -> "installed",
missing capitals, missing periods.
"""
from __future__ import annotations

import re

from spellchecker import SpellChecker

_spell = SpellChecker()

# Real shop jargon / abbreviations / asset-name fragments a general
# English dictionary doesn't know, which it would otherwise "correct"
# into the wrong word entirely. Extend this list as real false positives
# get reported in production -- deliberately not trying to be exhaustive
# up front (YAGNI); pyspellchecker already knows most plain English shop
# words (gasket, belt, bracket, motor, etc.) just fine on its own.
JARGON_WHITELIST = {
    "voidfill", "trkgate", "baler", "solenoid", "recalibrated",
    "recalibrate", "recalibration", "plc", "vrc", "rtu", "oos", "rts",
    "mst", "cetaris", "forklift",
    # Added 2026-09-19 after reviewing a full week of real shift output --
    # each of these got mangled into a WRONG word by the general
    # dictionary (not just an unhelpful guess -- an actively misleading
    # one), caught by manual review, never by a test in isolation:
    "retaped", "retape", "retaping",  # -> was becoming "reaped"
    "comm",                           # shop-radio/dash jargon -> was becoming "come"
    "refed", "refeed",                # "re-fed the paper" -> was becoming "refer"
    "ranpak",                         # equipment brand name -> was becoming "rank"
}

# Only correct plain alphabetic tokens -- never touch asset IDs (mixed
# letters+digits), numbers, or short function words where a "correction"
# is more likely to be a false positive than a real fix.
_MIN_CORRECTABLE_LEN = 3


# Real typos observed in actual shift data that pyspellchecker's general
# dictionary happens to NOT catch (its corpus contains "colum" as a rare
# known word, so it never flags it as misspelled). Checked before the
# general dictionary pass. Add to this as new confirmed real-world misses
# get reported -- don't guess ahead of actual evidence.
_KNOWN_TYPO_OVERRIDES = {
    "colum": "column",
    # Added 2026-09-19 after reviewing a full week of real shift output --
    # pyspellchecker's own best-guess correction for these was WRONG
    # (would have introduced a different incorrect word, not just left
    # the typo uncorrected):
    "tention": "tension",     # was becoming "mention"
    "adressed": "addressed",  # was becoming "dressed"
}


def _is_correctable(word: str) -> bool:
    if len(word) < _MIN_CORRECTABLE_LEN:
        return False
    if word.isupper():  # abbreviations/acronyms: WO, PM, RTS, TSP...
        return False
    if word.lower() in JARGON_WHITELIST:
        return False
    return True


# Splits into alternating runs of word-tokens / non-word-runs so the
# original string can be reassembled exactly except for corrected words --
# spacing, punctuation, numbers, and casing structure are all preserved
# untouched. A "word-token" includes an internal apostrophe (didn't,
# wasn't) so contractions stay a single unit -- splitting on the
# apostrophe used to treat "didn" as its own misspelled word and "correct"
# it to "did", silently turning "didn't" into "did't" (caught 2026-09-19
# reviewing real shift data). part.isalpha() is False for anything with
# an apostrophe, so correct_spelling() below now skips contractions
# entirely -- correct behavior, since contraction spelling is out of
# scope for this narrow word-for-word tool anyway.
_WORD_RE = re.compile(r"[A-Za-z]+(?:'[A-Za-z]+)*|[^A-Za-z]+")


def correct_spelling(text: str) -> str:
    """Fix individually misspelled words only. Deterministic: the same
    input always produces the same output. Never reorders, removes, or
    adds words -- only swaps a misspelled token for pyspellchecker's best
    guess at the correctly-spelled form, preserving original casing.
    """
    if not text or not text.strip():
        return text

    parts = _WORD_RE.findall(text)
    out: list[str] = []
    for part in parts:
        if part.isalpha() and _is_correctable(part):
            lower = part.lower()
            if lower in _KNOWN_TYPO_OVERRIDES:
                candidate = _KNOWN_TYPO_OVERRIDES[lower]
                part = candidate.capitalize() if part[0].isupper() else candidate
            elif lower not in _spell:
                candidate = _spell.correction(lower)
                if candidate and candidate != lower:
                    part = candidate.capitalize() if part[0].isupper() else candidate
        out.append(part)
    return "".join(out)

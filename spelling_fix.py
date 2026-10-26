"""Automatic ordinary-English spelling correction for IND3 maintenance text.

The original Cetaris text remains in the database. This is a display/report
transform for both Cause / Issue and Technician Remarks.

Correct common unambiguous English misspellings automatically; protect asset
numbers, acronyms, equipment terminology, numbers, hyphenated part codes,
personal names and plausible technical unknowns from dictionary guesses.

A dictionary cannot safely correct real-word homophones from context, so
those require narrowly scoped, reviewed phrase rules or the separately
validated local AI rewrite. Never synthesize a repair/outcome to fix grammar.
"""
from __future__ import annotations

import re
from spellchecker import SpellChecker

_spell = SpellChecker(distance=1)

JARGON_WHITELIST = {
    "asrs","autobagger","autobaggers","autocar","baler","balers",
    "cetaris","chock","chocks","chute","chutes","conveyance",
    "conveyor","conveyors","decant","drax","dock","encoder","flex",
    "flapcut","forklift","fsm","hmi","kisoft","knapp","loto",
    "leveler","levelers","mdr","mst","oos","osr","pe","plc","pm",
    "powercycled","ranpak","recalibrate","recalibrated",
    "recalibration","recirculation","refed","refeed","retape",
    "retaped","retaping","retreaded","retreading","rfr","rmt",
    "roller","rollers","rts","rtu","scada","sensor","sensors",
    "servo","servos","solenoid","solenoids","spurs","squeegee",
    "squeegees","tampad","taper","tapers","tapehead","tapeheads",
    "tapper","tensioner","tensioners","trkgate","vfd","voidfill","voidfills","vrc",
    "wcs","wo","wos","comm","tsp","mhe","rfid","autobaggers",
}
_spell.word_frequency.load_words(JARGON_WHITELIST)

# Human-reviewed single-token typos observed in Cetaris notes. These take
# precedence when several otherwise valid English candidates are nearby.
_KNOWN_TYPO_OVERRIDES = {
    "colum":"column","tention":"tension","adressed":"addressed",
    "frist":"first","sstep":"step","intstalled":"installed",
    "boixes":"boxes","boxws":"boxes","griper":"gripper",
    "griping":"gripping","haveing":"having","toren":"torn",
    "produck":"product","throught":"through","whated":"waited",
    "servor":"servo","occuring":"occurring","allignment":"alignment",
    "recalibratd":"recalibrated","papper":"paper","repalced":"replaced",
}

# Do not rewrite an otherwise valid English word out of context. A single
# contextual repair is safe where the surrounding phrase is very specific.
_PAPER_WRAP_RE = re.compile(
    r"\b(paper)\s+(got|was)\s+raped\s+(around|on)\b", re.I)
_TOKEN_RE = re.compile(
    r"[A-Za-z0-9]+(?:'[A-Za-z]+)*(?:[-_./][A-Za-z0-9]+)*|[^A-Za-z0-9]+")
_NAME_HINT_RE = re.compile(r"\b[A-Z][a-z]+\s*,\s*[A-Z][a-z]+\b")


def _preserve_case(original: str, corrected: str) -> str:
    if original.istitle():
        return corrected.capitalize()
    return corrected


def _correct_candidate(word: str) -> str:
    """Select only a high-confidence ordinary-English edit-distance-1 fix.

    Word frequency is used to avoid ambiguous auto-correction. Candidate
    selection is bounded to one character: 2-edit guesses can be semantic
    substitutions, particularly in maintenance reports.
    """
    lower = word.casefold()
    if lower in _KNOWN_TYPO_OVERRIDES:
        return _KNOWN_TYPO_OVERRIDES[lower]
    if len(lower) < 4 or lower in JARGON_WHITELIST or _spell.known([lower]):
        return lower
    candidates = [candidate for candidate in (_spell.candidates(lower) or set())
                  if candidate != lower and candidate.isalpha()]
    if not candidates:
        return lower
    ranked = sorted(candidates, key=lambda c: _spell.word_frequency[c], reverse=True)
    best = ranked[0]
    top = _spell.word_frequency[best]
    next_best = _spell.word_frequency[ranked[1]] if len(ranked)>1 else 0
    # An ambiguous edit must remain unchanged rather than silently altering
    # a technician's meaning. Common ordinary-English typos are covered by
    # high-confidence candidates or reviewed overrides.
    if top <= 0 or (next_best and top < 4 * next_best):
        return lower
    return best


def correct_spelling(text: str) -> str:
    """Correct ordinary English in either report field without changing facts."""
    if not text or not text.strip():
        return text
    protected_names = {}
    def protect_name(match):
        placeholder = f"Zznameplaceholder{len(protected_names)}Zz"
        protected_names[placeholder] = match.group()
        return placeholder
    # Protect surname-first technician citations where present in notes.
    text = _NAME_HINT_RE.sub(protect_name, text)
    text = _PAPER_WRAP_RE.sub(
        lambda m: f"{m.group(1)} {m.group(2)} wrapped {m.group(3)}", text)
    out = []
    for token in _TOKEN_RE.findall(text):
        # The tokenizer yields intact WO numbers/part IDs/hyphenated codes.
        if (not token.isalpha() or token.isupper() or
            not (token.islower() or token.istitle())):
            out.append(token)
            continue
        corrected = _correct_candidate(token)
        out.append(_preserve_case(token,corrected))
    result = "".join(out)
    for placeholder, source in protected_names.items():
        result = result.replace(placeholder, source)
    return result

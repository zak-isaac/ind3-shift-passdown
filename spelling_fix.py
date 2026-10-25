"""IND3 safe, deterministic spelling corrections.

The general English spellchecker is useful for identifying possible typos,
but its most-likely guess can change technical meaning ("chute", "rollers",
"levelers", etc.). Consequently, V3 automatically applies ONLY vetted typo
substitutions. Anything unfamiliar remains unchanged. No MST approval is
required, and there are no network/model calls.

Corrections and terminology can be extended only with reviewed evidence from
real maintenance notes and a corresponding regression test.
"""
from __future__ import annotations

import re
from spellchecker import SpellChecker

# Keep an English-language dictionary alongside the IND3 technical lexicon.
# It supports validating candidate additions, NOT unrestricted replacement.
_spell = SpellChecker(distance=1)

# Real equipment/components, brands and shop shorthand are intentionally
# protected; unknown words also pass through unchanged.
JARGON_WHITELIST = {
    "asrs", "autobagger", "baler", "balers", "baler", "cetaris", "chock",
    "chocks", "chute", "chutes", "conveyor", "conveyors", "decant",
    "dock", "encoder", "flex", "flapcut", "forklift", "fsm", "hmi",
    "kisoft", "knapp", "loto", "leveler", "levelers", "mdr", "mst",
    "oos", "osr", "pe", "plc", "pm", "powercycled", "ranpak",
    "recalibrate", "recalibrated", "recalibration", "recirculation",
    "refed", "refeed", "retape", "retaped", "retaping", "retreaded",
    "retreading", "rfr", "rmt", "rollers", "roller", "rts", "rtu",
    "scada", "sensor", "sensors", "servo", "servos", "solenoid",
    "solenoids", "spurs", "squeegee", "squeegees", "tampad", "tapper",
    "trkgate", "vfd", "voidfill", "voidfills", "vrc", "wcs", "wo", "wos",
    "comm", "tsp",
}
_spell.word_frequency.load_words(JARGON_WHITELIST)

# Safe, confirmed typo -> spelling pairs only. Do not add shorthand expansions
# that alter the meaning, asset status or claims about a completed repair.
_KNOWN_TYPO_OVERRIDES = {
    "colum": "column",
    "tention": "tension",
    "adressed": "addressed",
    "frist": "first",
    "sstep": "step",
    "intstalled": "installed",
}

# Never split an asset, model, WO number, component code or hyphenated
# technical identifier into separate English-looking pieces. Example:
# "bager0013" must NOT become "eager0013".
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z]+)*(?:[-_./][A-Za-z0-9]+)*|[^A-Za-z0-9]+")


def _is_correctable(word: str) -> bool:
    return (word.isalpha() and word.islower() or
            word.isalpha() and word.istitle()) and word.casefold() not in JARGON_WHITELIST


def correct_spelling(text: str) -> str:
    """Correct only reviewed typos while preserving every other character."""
    if not text or not text.strip():
        return text
    out = []
    for token in _TOKEN_RE.findall(text):
        lower = token.casefold()
        if _is_correctable(token) and lower in _KNOWN_TYPO_OVERRIDES:
            fix = _KNOWN_TYPO_OVERRIDES[lower]
            token = fix.capitalize() if token[0].isupper() else fix
        out.append(token)
    return "".join(out)

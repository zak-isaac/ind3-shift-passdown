"""Conservative automatic proofreader for V3 maintenance notes.

Only spelling, punctuation spacing and sentence capitalization are adjusted.
Original Cetaris imports are stored separately; this transforms displayed and
reportable text, never the source workbook. No generative paraphrasing.
"""
from __future__ import annotations
import re

from spelling_fix import correct_spelling
from text_normalize import fix_punctuation_spacing, fix_sentence_capitalization

_START_RE = re.compile(r"^(\s*)([a-z])", re.MULTILINE)


def proofread(value: str) -> str:
    if not value or not isinstance(value, str):
        return value
    text = correct_spelling(value)
    # Standalone first-person "i" is ordinary English, but never rewrite an
    # identifier (MDR0016, VFD, O-Ring), model number or part code.
    text = re.sub(r"(?<![A-Za-z0-9])i(?![A-Za-z0-9])", "I", text)
    text = fix_punctuation_spacing(text)
    text = fix_sentence_capitalization(text)
    text = _START_RE.sub(lambda match: match.group(1) + match.group(2).upper(), text)
    return text

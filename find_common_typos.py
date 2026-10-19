"""Non-AI tool: scan historical remarks for likely recurring typos.

Usage: .venv\\Scripts\\python.exe find_common_typos.py [--db dbs/ind3.db]

Prints words pyspellchecker flags as unknown that appear more than once
across real historical remarks, sorted by frequency. This is a review
tool, not an auto-fixer -- a word appearing twice could be a real typo
(add to spelling_fix._KNOWN_TYPO_OVERRIDES) or legit shop jargon worth
adding to the JARGON_WHITELIST instead. Human judgment stays in the loop
on purpose; this just surfaces candidates instead of guessing blind.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter

from spellchecker import SpellChecker

from spelling_fix import JARGON_WHITELIST, _KNOWN_TYPO_OVERRIDES, _is_correctable, _WORD_RE


def _iter_remarks(db_path: str):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for row in conn.execute("SELECT raw_remarks, issue FROM entries"):
        for remark in json.loads(row["raw_remarks"] or "[]"):
            if remark:
                yield remark
        if row["issue"]:
            yield row["issue"]
    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="dbs/ind3.db")
    parser.add_argument("--min-count", type=int, default=2)
    args = parser.parse_args()

    spell = SpellChecker()
    counts: Counter[str] = Counter()

    for remark in _iter_remarks(args.db):
        for part in _WORD_RE.findall(remark):
            if not part.isalpha() or not _is_correctable(part):
                continue
            lower = part.lower()
            if lower in _KNOWN_TYPO_OVERRIDES or lower in JARGON_WHITELIST:
                continue
            if lower not in spell:
                counts[lower] += 1

    print(f"{'word':<20}{'count':<8}{'suggested fix'}")
    for word, count in counts.most_common():
        if count < args.min_count:
            break
        print(f"{word:<20}{count:<8}{spell.correction(word) or '?'}")


if __name__ == "__main__":
    main()

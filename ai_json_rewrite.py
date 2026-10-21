"""Structured, repeatable JSON-mode rewrite path -- EXPERIMENTAL, isolated
from the live production pipeline in ai_rewriter.py.

Why a separate module instead of editing ai_rewriter.py in place:
- ai_rewriter.py's free-text pipeline is the current PRODUCTION path
  (_USE_AI is currently False -- deterministic cleanup only, per Isaac's
  2026-09-19 decision). Nothing in this file changes that; _USE_AI is
  never imported or touched here.
- This module reuses ai_rewriter's already-hardened building blocks
  (build_input, the fabrication/dropped-token/direction-reversal guards,
  the deterministic fallback, the HTTP client) instead of duplicating
  them -- DRY, and it means every existing guard still applies.
- Keeping this isolated means it can be evaluated and thrown away freely
  without risking the production prompt/guard chain that took multiple
  live-incident-driven iterations to get right (see ai_rewriter.py's
  extensive dated comments).

Model note (2026-09-22): gemma4:e4b is confirmed present in `ollama list`
but is NOT usable -- Ollama 0.9.4's bundled llama.cpp engine has no code
for the 'gemma4' architecture yet ("unknown model architecture: 'gemma4'",
confirmed via C:\\Users\\ins0002\\AppData\\Local\\Ollama\\server.log). It is
left installed, untouched, for whenever Ollama is updated. This module
targets gemma3n-e2b instead -- confirmed via `ollama list` (exact tag
"gemma3n-e2b:latest") before use, not assumed.

Public API
----------
rewrite_structured(asset_id, wo_number, remarks) -> StructuredResult
    Calls Ollama in JSON mode with temperature=0.0 and a fixed seed,
    validates the result against the source text, and falls back to the
    raw technician remarks (joined, unmodified) if anything looks wrong.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import ai_rewriter as ar

log = logging.getLogger(__name__)

# Fixed for repeatability testing -- see requirements: temperature=0.0 (pure
# greedy decoding, no sampling) + a pinned seed. Note this does NOT guarantee
# byte-exact output on every possible model/hardware combination (CPU
# multi-threaded float summation can still introduce tiny nondeterminism --
# see the repeatability test's tolerance for functional-not-byte-exact
# comparison), but it removes the two biggest, controllable sources of
# run-to-run variation. Thread config deliberately left untouched (per
# explicit instruction) -- ai_rewriter's existing OLLAMA/httpx client
# config already governs concurrency/threads and this module reuses it.
_TEMPERATURE = 0.0
_SEED = 42

# CRITICAL, discovered live (2026-09-22): without an explicit num_ctx,
# Ollama's runtime default context window is far smaller than gemma3n-e2b's
# supported max (32768, confirmed via `ollama show gemma3n-e2b`) -- and far
# smaller than this module's ~12.5K-char (~3000+ token) system prompt needs.
# Overflow doesn't error cleanly; it PANICS the llama runner process itself
# ("failed to decode batch: could not find a kv cache slot"), which Ollama
# then silently restarts, producing a mix of real successes and bare 500s
# from the same identical input -- exactly the same failure shape as the
# qwen3-4b num_ctx incident documented in ai_rewriter.py. Same fix: set
# num_ctx explicitly, generously above the actual prompt+response size.

# Measured live (2026-09-22, warm model): the full ~12.5K-char production
# system prompt combined with JSON-grammar-constrained decoding took ~75s
# on this CPU-only box, well past ai_rewriter's free-text _TIMEOUT_SINGLE
# (60s, tuned for a much shorter prompt/no grammar constraint). Grammar-
# constrained sampling has real per-token overhead beyond plain generation.
# Giving this its own, longer, dedicated timeout rather than reusing the
# free-text pipeline's -- they're tuned for genuinely different workloads.
_JSON_TIMEOUT = 150.0
_NUM_CTX = 8192

# gemma4:e4b is blocked (see module docstring) -- confirmed via `ollama
# list` on 2026-09-22, not assumed. Do not change this back until Ollama
# itself is updated to a version whose llama.cpp supports 'gemma4'.
DEFAULT_MODEL = "gemma3n-e2b"

_JSON_SCHEMA_ADDENDUM = """
Output Format
Respond with ONLY a single JSON object (no prose before or after it, no
markdown code fences) matching exactly this schema:

{
  "asset_id": "<the asset ID exactly as given, or null if none was given>",
  "actions_performed": ["<one specific action or finding per array entry>"],
  "unresolved_issues": ["<any issue explicitly left unresolved -- empty array if none>"],
  "returned_to_service": true, false, or null,
  "narrative": "<the same flowing passdown paragraph the rules above describe>"
}

Rules for the JSON fields specifically:
- "asset_id" must be the EXACT asset name/identifier as given in the input
  -- copy it verbatim, character for character. Never invent, guess,
  complete, modify, abbreviate, or expand it, and never replace it with a
  generic term like "unit", "machine", or "equipment". If the input does
  not contain an asset name, "asset_id" must be null -- do not guess one.
- "actions_performed" must list only actions/findings actually stated in the
  input. Never add a step, cause, part, or verification that was not written.
- "unresolved_issues" must be empty ([]) unless the input explicitly leaves
  something unresolved, broken, pending, or unverified.
- "returned_to_service" must be true ONLY if the input explicitly says the
  asset was returned to service, is operating normally, running properly,
  back in service, or equivalent. It must be false ONLY if the input
  explicitly says the asset remains down, unavailable, unresolved, or out
  of service. Otherwise it MUST be null.
  CRITICAL: completing repair work, replacing a part, or fixing something
  is NOT the same as stating operational status. Never infer
  returned_to_service merely because a repair or fix was performed --
  only explicit status language counts.
  Example: "Recalibrated each floor" describes an action only, with no
  stated outcome -- the correct value is null, NOT true, even though the
  action sounds complete.
  Example: "Calibrated both closers. Asset is operational now." explicitly
  states the outcome -- true is correct here because of "is operational
  now", not because calibration was performed.
- "narrative" follows every rule above -- same technician vocabulary, no
  invented subject, no fabricated detail, single continuous paragraph.
  Do not add words implying success, completion, or finality ("completed",
  "successfully", "resolved", "fixed") unless the source text itself uses
  that language -- describing an action is not the same as declaring its
  outcome. Same rule as returned_to_service above, applied to word choice.
Do not invent a work order number anywhere in the output. This system does
not give you one, so any number resembling a work order ID in your output
is guaranteed to be fabricated.
"""

# Reuse the production system prompt's rules verbatim, swapping only the
# final "output only the rewritten note" instruction for the JSON schema
# above -- avoids duplicating (and risking drift from) dozens of hard-won
# fabrication-prevention rules.
_JSON_SYSTEM_PROMPT = (
    ar._SYSTEM_PROMPT.rsplit("Output only the rewritten note. Nothing else.", 1)[0]
    + _JSON_SCHEMA_ADDENDUM
)

_JSON_TEMPLATE = (
    "Rewrite the following technician remark(s) into the JSON schema described"
    " above. If more than one remark is separated by ' | ', they are sequential"
    " entries for the same issue -- synthesize them into ONE continuous"
    " narrative, not a list. Follow all rules exactly."
    " The 'narrative' field must not include the asset ID anywhere in it,"
    " not even mid-sentence -- the report already shows it separately; use"
    " 'the asset' or 'it' instead if a subject is needed.\n"
    "{context_block}"
    "---\n"
    "Original: {text}\n"
    "JSON:"
)

# Matches Cetaris-style work order numbers (7-9 digit sequences) so we can
# catch a hallucinated one anywhere in the model's output. Never intended to
# match legitimate short numbers like measurements or quantities.
_WO_NUMBER_RE = re.compile(r"\b\d{7,9}\b")

# Conservative keyword sets for detecting whether the SOURCE text actually
# states a returned-to-service status one way or the other. Deliberately
# narrow -- false negatives (missing a real status statement) just mean a
# valid true/false gets rejected as unstated, which is safe/annoying but
# never lets a fabricated status through, which is the actual risk.
_RTS_TRUE_PHRASES = (
    "returned to service", "return to service", "back in service",
    "operational now", "is operational", "fully operational",
    "back up", "back online", "rts", "operating normally",
    "running properly", "running normally", "working properly",
    "working normally", "functioning normally", "functioning properly",
)
_RTS_FALSE_PHRASES = (
    "out of service", "still down", "not operational", "remains down",
    "down for", "awaiting parts", "awaiting replacement", "unavailable",
    "unresolved", "not resolved", "still not working", "still broken",
    "still not operational", "faults remain", "fault remains",
    "issue remains", "issues remain", "problem remains",
)

# 2026-09-23 defect fix #2: plain substring matching above is vulnerable to
# two distinct failure modes exposed by the [ASSET]-placeholder test matrix:
#   1. Negation: "is NOT operating normally" still contains "operating
#      normally" as a substring, so naive matching returned True on an
#      explicitly negated statement -- backwards.
#   2. Embedded asset name: "Unable to return BALER0002 to service" breaks
#      the contiguous phrase "return to service" in two, so it silently
#      matched NOTHING (should be False, not None).
#   3. A true phrase can be reversed by a LATER contradicting clause in the
#      same sentence: "was returned to service but faulted again" starts
#      with a true-phrase match yet the overall statement is false.
# All three are handled below without a full NLP dependency -- deliberately
# narrow, pattern-based fixes justified by concrete failing test cases, not
# a general negation/discourse parser.
_NEGATION_WORDS = {
    "not", "never", "unable", "cannot", "cant", "wont", "doesnt", "didnt",
    "isnt", "wasnt", "arent", "werent", "couldnt", "wouldnt", "shouldnt",
    "hasnt", "hadnt", "havent", "no",
}
_CONTRAST_WORDS = {"but", "however", "although", "yet", "though"}
_RENEWED_FAILURE_WORDS = {
    "faulted", "failed", "broke", "broken", "down", "stopped", "quit",
    "died", "malfunctioned", "failing", "faulting", "faulted",
}


def _phrase_negated(text: str, phrase: str, window: int = 4) -> bool:
    """True if a negation word appears in the `window` words immediately
    before `phrase`. English negation typically precedes what it negates
    ("not operating normally", "never returned to service"), so a
    backward-only window catches the common cases without full parsing."""
    idx = text.find(phrase)
    if idx == -1:
        return False
    before = text[:idx].split()
    return any(w.strip(".,;:") in _NEGATION_WORDS for w in before[-window:])


def _true_phrase_overridden(text: str, phrase: str, window: int = 6) -> bool:
    """True if a contrast word ("but", "however", ...) appears anywhere
    after `phrase`, followed within `window` words by a renewed-failure
    word -- catches "returned to service but faulted again"-style reversals
    that a plain phrase match would otherwise accept at face value."""
    idx = text.find(phrase)
    if idx == -1:
        return False
    after = text[idx + len(phrase):].split()
    for i, w in enumerate(after):
        if w.strip(".,;:") in _CONTRAST_WORDS:
            remainder = after[i + 1:i + 1 + window]
            if any(rw.strip(".,;:") in _RENEWED_FAILURE_WORDS for rw in remainder):
                return True
    return False

# 2026-09-23 defect fix: gemma3n-e2b was found (100% reproducible, 9/9 runs
# on the 'simple_one_liner' case) to infer returned_to_service=True purely
# because a repair/adjustment action was described ("Recalibrated each
# floor"), even with zero status language present. Completing work is NOT
# the same as stating operational status -- _source_states_return_to_service
# deliberately never looks at action verbs (replaced/fixed/recalibrated/
# etc.), only explicit status phrases, so this function's None return
# already causes validate_structured_output() to reject any true/false the
# model emits here as an invented fact regardless of the model's reasoning.
# The phrase-list expansion above and the strengthened prompt instructions/
# examples in _JSON_SCHEMA_ADDENDUM are the actual defect fix; this function
# itself was already correct and needed no logic change.


def _source_states_return_to_service(source_text: str, asset_id: str = "") -> bool | None:
    """Deterministic, source-derived returned_to_service classifier -- the
    single source of truth for this field (see validate_structured_output,
    which always overwrites the model's guess with this function's answer).
    asset_id is stripped from the text first so phrasing like "Unable to
    return BALER0002 to service" doesn't break the "return to service"
    phrase into two non-contiguous pieces -- the asset's identity is
    validated completely separately (exact-match on the asset_id field);
    this stripping is purely to normalize phrasing for status matching."""
    text = source_text.lower()
    if asset_id:
        text = text.replace(asset_id.lower(), " ")
    text = re.sub(r"\s+", " ", text)

    for phrase in _RTS_TRUE_PHRASES:
        if phrase in text:
            if _phrase_negated(text, phrase):
                return False
            if _true_phrase_overridden(text, phrase):
                return False
            return True
    for phrase in _RTS_FALSE_PHRASES:
        if phrase in text:
            return False if not _phrase_negated(text, phrase) else True
    return None


@dataclass
class StructuredResult:
    asset_id: str | None
    actions_performed: list[str] = field(default_factory=list)
    unresolved_issues: list[str] = field(default_factory=list)
    returned_to_service: bool | None = None
    narrative: str = ""
    raw_json: str = ""
    valid: bool = False
    fallback_used: bool = False
    # Distinct, explicit lists (not one merged string) so the repeatability
    # report can show missing-facts and invented-facts as separate sections,
    # per explicit reporting requirements.
    missing_facts: list[str] = field(default_factory=list)
    invented_facts: list[str] = field(default_factory=list)
    rejection_reason: str = ""
    # Model-vs-deterministic RTS disagreements, logged for audit visibility
    # only -- these never affect `valid` because returned_to_service is
    # always overwritten with the deterministic, source-derived value (see
    # validate_structured_output). Distinct from invented_facts, which
    # tracks fabrications that DO survive into the output and DO fail
    # validation.
    corrected_fields: list[str] = field(default_factory=list)


def _call_ollama_json(prompt: str, model: str, num_predict: int = 300) -> str | None:
    """Same transport as ai_rewriter._call_ollama, but JSON mode + fixed
    temperature/seed for repeatability. Kept separate rather than adding
    format=json as an optional param to the production function, since the
    production function is intentionally not touched by this experimental
    path."""
    try:
        resp = ar._get_client().post(
            "/api/generate",
            json={
                "model":  model,
                "system": _JSON_SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
                "format": "json",
                "keep_alive": ar._KEEP_ALIVE,
                "options": {
                    "temperature": _TEMPERATURE,
                    "seed": _SEED,
                    "num_predict": num_predict,
                    "num_ctx": _NUM_CTX,
                    "stop": ["\n\n\n"],
                },
            },
            timeout=_JSON_TIMEOUT,
        )
        resp.raise_for_status()
        result = resp.json().get("response", "").strip()
        return result or None
    except Exception as exc:
        log.warning("[ai_json_rewrite] Error: %s", exc)
        return None


def _parse_json_response(raw: str) -> dict | None:
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return parsed


# Generic structural/referent words the prompt itself instructs the model to
# use (e.g. rule 26: "Always use 'asset'...", and this module's own
# instruction to say "the asset" instead of repeating the asset ID) -- these
# are never domain facts, so they must never be flagged as invented content.
_STRUCTURAL_WORDS = {"asset", "it", "unit", "equipment"}

# Pure discourse/prose connectors -- add zero factual content on their own,
# so flagging them as "invented" was a false-positive source found in
# testing (2026-09-23: "however" flagged as fabrication in a narrative that
# was actually a faithful paraphrase). Deliberately excludes words that
# carry real sequencing/causal meaning relevant to maintenance facts (e.g.
# "before"/"after"/"since" are left OUT -- ai_rewriter._new_old_direction_
# reversed already guards those specifically, and this generic check
# shouldn't quietly wave through a reversed-order claim).
_DISCOURSE_CONNECTORS = {
    "however", "although", "meanwhile", "additionally", "furthermore",
    "therefore", "consequently", "also", "overall",
}

# Common English irregular verb forms that don't share a prefix with their
# base form (e.g. find/found, is/was) -- _same_word_family's prefix check
# can't catch these. Curated, not exhaustive (YAGNI): just enough to stop
# real false positives seen in testing, where "couldn't find" paraphrased
# as "was not found" flagged "found" as invented purely because find/found
# share no prefix.
_IRREGULAR_VERB_FAMILIES = [
    {"find", "found"}, {"is", "was", "were", "been", "being", "are"},
    {"go", "went", "gone"}, {"do", "did", "done"}, {"have", "has", "had"},
    {"see", "saw", "seen"}, {"get", "got", "gotten"}, {"make", "made"},
    {"take", "took", "taken"}, {"bring", "brought"}, {"catch", "caught"},
    {"leave", "left"}, {"lose", "lost"}, {"break", "broke", "broken"},
    {"throw", "threw", "thrown"}, {"wear", "wore", "worn"},
    {"come", "came"}, {"run", "ran"}, {"give", "gave", "given"},
    {"send", "sent"}, {"build", "built"}, {"keep", "kept"},
]

# ai_rewriter._stem() is deliberately a narrow singular/plural-only stemmer
# (strips a trailing 's'), tuned specifically for _dropped_part_nouns's part-
# noun matching -- it does NOT normalize verb forms (replace/replaced/
# replacement all stay distinct). A first attempt at a suffix-stripping
# stemmer here still broke on short roots + '-ed'/'-ing' ("wire" -> "wire"
# but "wired" -> "wired", since stripping "ed" from a 5-letter word leaves
# "wir", not "wire" -- the silent-e problem). Pairwise prefix containment
# sidesteps English morphology entirely and handles both cases correctly.
def _same_word_family(a: str, b: str, min_len: int = 4) -> bool:
    """True if one word is a prefix of the other, using at least min_len
    characters -- catches verb-form/suffix variation (replace/replaced/
    replacement, wire/wired, recalibrate/recalibrated) without needing real
    morphological analysis. Words shorter than min_len must match exactly,
    to avoid trivial collisions between short unrelated words. Also checks
    the curated irregular-verb table above, since prefix matching alone
    can't catch find/found-style pairs."""
    if a == b:
        return True
    for family in _IRREGULAR_VERB_FAMILIES:
        if a in family and b in family:
            return True
    if len(a) < min_len or len(b) < min_len:
        return a == b
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer.startswith(shorter)


def _detect_invented_words(source_text: str, narrative: str, asset_id: str = "") -> list[str]:
    """Generic added-content heuristic, complementary to ai_rewriter's
    existing dropped-content guards. Reuses ai_rewriter's own tokenizer
    (_significant_words) for consistency, but matches word families via
    _same_word_family (above) instead of ai_rewriter._stem, which is too
    narrow for this purpose. Conservative by design: only flags a narrative
    word as 'invented' if NO source word is in the same family, so ordinary
    paraphrasing/word-form changes are never false-flagged. asset_id's own
    words are always allowed -- the model referencing the asset it was told
    about isn't fabrication."""
    source_words = ar._significant_words(source_text) | ar._significant_words(asset_id)
    narrative_words = ar._significant_words(narrative)

    # Bare "not" is intentionally NOT in _DISCOURSE_CONNECTORS -- negation
    # words are exactly the high-risk category this checker should stay
    # suspicious of (a fabricated "not" can silently reverse a fact). But
    # ar._significant_words() splits on non-letters, so a source contraction
    # like "couldn't" tokenizes to just "couldn" (the "t" is dropped, being
    # length 1) -- meaning a faithful paraphrase ("couldn't find the issue"
    # -> "the issue was not found") was false-flagged in testing (2026-09-
    # 23) purely because the negation was spelled differently, not invented.
    # Only allow "not" through when the source demonstrably already
    # contains a negation via one of these unambiguous contraction spellings
    # (both with and without the apostrophe, since technician remarks in
    # this dataset are consistently apostrophe-free) -- deliberately a
    # narrow, curated list, not a general negation detector, so a genuinely
    # invented "not" with no negation anywhere in the source still gets
    # caught.
    _NEGATION_CONTRACTIONS = {
        "couldnt", "couldn", "wouldnt", "wouldn", "shouldnt", "shouldn",
        "doesnt", "doesn", "didnt", "didn", "isnt", "isn", "wasnt", "wasn",
        "arent", "aren", "werent", "weren", "hasnt", "hasn", "hadnt", "hadn",
        "havent", "haven", "cannot", "dont",
    }
    source_has_negation = bool(source_words & _NEGATION_CONTRACTIONS)

    invented = sorted(
        w for w in narrative_words
        if w not in ar._GENERIC_ACTIVITY_WORDS
        and w not in _STRUCTURAL_WORDS
        and w not in _DISCOURSE_CONNECTORS
        and not (w == "not" and source_has_negation)
        and not any(_same_word_family(w, sw) for sw in source_words)
    )
    return invented


def validate_structured_output(
    parsed: dict, source_text: str, asset_id: str, wo_number: str,
) -> StructuredResult:
    """Programmatic validation. Builds a full StructuredResult (not just a
    pass/fail bool) so the repeatability report can show missing facts,
    invented facts, and the specific rejection reason side by side.
    Per requirements:
    - work order numbers in the output must match the source
    - asset names in the output must match the source
    - reject output containing facts unsupported by the source (reuses
      ai_rewriter's existing, production-proven fabrication guards)
    - flag any invented diagnosis/part/action/status/work-order number
    """
    result = StructuredResult(
        asset_id=parsed.get("asset_id", asset_id) or asset_id,
        actions_performed=list(parsed.get("actions_performed", []) or []),
        unresolved_issues=list(parsed.get("unresolved_issues", []) or []),
        returned_to_service=parsed.get("returned_to_service"),
        narrative=str(parsed.get("narrative", "")).strip(),
    )
    narrative = result.narrative

    if not narrative:
        result.rejection_reason = "missing or empty 'narrative' field"
        return result

    # -- Asset name must match source ---------------------------------------
    # 2026-09-23 defect: flagging a mismatch in invented_facts alone did NOT
    # correct result.asset_id itself -- a fabricated/changed/completed asset
    # name could still sit in the result object even though it was flagged.
    # Per explicit requirement ("reject the AI output if the asset is
    # invented, changed, or removed"), this field is never model-trusted:
    # always force it to the caller-supplied ground truth (or None if the
    # caller supplied none), the same deterministic-override pattern used
    # for returned_to_service. Unlike returned_to_service, a mismatch here
    # is NOT downgraded to corrected_fields -- an invented/changed/removed
    # asset identity is always a serious, always-wrong condition (not the
    # common, expected ambiguity RTS often has), so it stays in
    # invented_facts and correctly fails validation.
    out_asset = parsed.get("asset_id")
    if out_asset not in (None, "", asset_id):
        result.invented_facts.append(f"asset_id: source={asset_id!r} output={out_asset!r}")
    result.asset_id = asset_id or None

    # Defense in depth, matching ai_rewriter's own _strip_leading_asset_id
    # safety net: the prompt instructs the model not to repeat the asset ID
    # inside the narrative (report shows it separately), but instructions
    # alone aren't reliable enough to trust without a programmatic check too.
    if asset_id and asset_id.lower() in narrative.lower():
        result.invented_facts.append(f"asset_id {asset_id!r} leaked into narrative text")

    # -- returned_to_service must never be an inferred status in the final
    # output. 2026-09-23 defect + follow-up fix: prompt strengthening alone
    # (explicit rule + worked examples) was NOT enough to stop gemma3n-e2b
    # guessing this field on ~2/8 test cases even after the fix -- it kept
    # inferring a status (in either direction) whenever the narrative tone
    # sounded resolved or unresolved, despite explicit instructions not to.
    # Since _source_states_return_to_service() is already a fully
    # deterministic, reliable classifier for this exact field, the robust
    # fix is to stop trusting the model's guess entirely: ALWAYS overwrite
    # the field with the deterministic value below, rather than merely
    # rejecting a bad guess and losing an otherwise-good narrative to a
    # full fallback over one field. A model/source disagreement is still
    # logged (to corrected_fields, not invented_facts) purely for audit
    # visibility into how often the model gets this field wrong -- it never
    # affects `valid` or the final returned_to_service value.
    source_rts = _source_states_return_to_service(source_text, asset_id)
    model_rts = result.returned_to_service
    if model_rts is not None and source_rts is None:
        result.corrected_fields.append(
            f"returned_to_service: model guessed {model_rts} with no explicit supporting "
            f"source text -- overwritten with null (source never states equipment status)"
        )
    elif model_rts is not None and model_rts != source_rts:
        result.corrected_fields.append(
            f"returned_to_service: model guessed {model_rts}, contradicting source "
            f"(source implies {source_rts}) -- overwritten with the source-derived value"
        )
    result.returned_to_service = source_rts

    # -- Work order numbers: any that appear must match the source exactly --
    all_text_fields = [narrative] + [str(x) for x in result.actions_performed] \
        + [str(x) for x in result.unresolved_issues]
    for f in all_text_fields:
        for candidate in _WO_NUMBER_RE.findall(f):
            if not (wo_number and candidate == wo_number):
                result.invented_facts.append(f"work order number: {candidate!r}")

    # -- Reuse ai_rewriter's existing, production-proven fabrication guards -
    # on the narrative field. Deliberately NOT reimplementing "does this add
    # unsupported facts" from scratch -- these guards already encode a full
    # week of real-incident-driven fixes.
    if ar._claims_unwarranted_resolution(source_text, narrative):
        result.invented_facts.append("resolution/outcome not stated in source")
    if ar._new_old_direction_reversed(source_text, narrative):
        result.invented_facts.append("reversed new/old or before/after direction")
    if ar._uses_wrong_domain_vocabulary(source_text, narrative):
        result.invented_facts.append("vocabulary outside the maintenance domain")

    stripped_labels = ar._strip_invented_component_labels(narrative, source_text)
    if stripped_labels != narrative:
        result.invented_facts.append("invented component/part label")
    stripped_equip = ar._strip_invented_equipment_type(narrative, asset_id, source_text)
    if stripped_equip != narrative:
        result.invented_facts.append("invented equipment type (e.g. vehicle/forklift)")

    generic_invented_words = _detect_invented_words(source_text, narrative, asset_id)
    if generic_invented_words:
        result.invented_facts.append(f"unrecognized new word(s): {generic_invented_words}")

    dropped = ar._dropped_key_tokens(source_text, narrative, asset_id)
    if dropped:
        result.missing_facts.append(f"dropped key tokens: {sorted(dropped)}")
    dropped_parts = ar._dropped_part_nouns(source_text, narrative)
    if dropped_parts:
        result.missing_facts.append(f"dropped part nouns: {sorted(dropped_parts)}")

    result.valid = not result.invented_facts and not result.missing_facts
    if not result.valid:
        reasons = result.invented_facts + result.missing_facts
        result.rejection_reason = "; ".join(reasons)
    return result


def rewrite_structured(asset_id: str, wo_number: str, remarks: list[str],
                        model: str = DEFAULT_MODEL) -> StructuredResult:
    """Build input, call Ollama in JSON mode, validate, and fall back to the
    raw technician remarks (never a deterministic *rewrite*, just the
    untouched source) if anything fails -- per the explicit fallback
    requirement."""
    combined_input = ar.build_input(asset_id, remarks)
    body = combined_input.split(" / ", 1)[-1] if " / " in combined_input else combined_input
    context_block = ar._asset_type_context(asset_id)
    prompt = _JSON_TEMPLATE.format(text=combined_input, context_block=context_block)

    fallback_narrative = " | ".join(r.strip() for r in remarks if r.strip())

    raw = _call_ollama_json(prompt, model=model)
    if raw is None:
        return StructuredResult(
            asset_id=asset_id, narrative=fallback_narrative, fallback_used=True,
            rejection_reason="Ollama call failed or timed out",
        )

    parsed = _parse_json_response(raw)
    if parsed is None:
        return StructuredResult(
            asset_id=asset_id, narrative=fallback_narrative, fallback_used=True,
            raw_json=raw, rejection_reason="model did not return valid JSON",
        )

    result = validate_structured_output(parsed, body, asset_id, wo_number)
    result.raw_json = raw
    if not result.valid:
        # FULL reset on fallback, not just the narrative. 2026-09-23 defect:
        # a fabricated field used to survive into the result even after
        # fallback replaced the narrative text. Once ANY field fails
        # validation, none of the model's FREE-FORM structured fields can
        # be trusted (the fabrication might not be confined to the field
        # that tripped the specific guard that caught it), so those reset
        # to safe/empty values. Two fields are deliberately excluded from
        # this reset because neither is ever model-trusted data in the
        # first place: asset_id is forced to equal the known-good input (or
        # flagged as invented), and returned_to_service is now ALWAYS the
        # deterministic, source-derived value set above in
        # validate_structured_output -- never the model's guess -- so it
        # stays correct regardless of what else on this call went wrong.
        result.actions_performed = []
        result.unresolved_issues = []
        result.narrative = fallback_narrative
        result.fallback_used = True
    return result

"""Local AI remark rewriter -- Ollama + Phi-4 Mini.

Public API
----------
check_ollama() -> bool
rewrite(text: str) -> str
rewrite_batch(inputs: list[str]) -> list[str]
    inputs format (see build_input()): "ASSET_ID / remark1 | remark2 | ..."
    The " | " separator tells the AI these are sequential remarks logged over
    time for the same issue -- they must be synthesized into one continuous
    narrative, not left as separate fragments.
    Output does NOT include the asset ID (report already shows it).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import httpx

import spelling_fix
import text_normalize

# -- Configuration -------------------------------------------------------------
# 2026-09-19: Isaac's explicit final call, after a full night of testing --
# he wants deterministic spelling/punctuation/grammar cleanup only, never
# AI paraphrasing, with a hard guarantee that the same input always gives
# the same output. Flip to True to re-enable the AI escalation path below
# (kept fully intact, just dormant) once there's time to make a generative
# model actually honor "minimal edit, same output every time" -- something
# no model tried tonight (phi4-mini, qwen3-4b) reliably did.
_USE_AI = False
_OLLAMA_BASE    = "http://localhost:11434"
# Live testing on shop hardware shows llama3.1:8b routinely takes 60-90s+ per
# call (mostly prompt-eval of the large system prompt, not generation) --
# 90s was clipping legitimate in-flight calls, which silently dumped the
# technician's raw, un-cleaned remark into the report (see rewrite()'s
# fallback path). Give it real headroom instead of pretending it's fast.
# phi4:14b (CPU-only, no real GPU on this box) is slower still and highly
# variable -- live testing on real shift-68 data showed multi-remark
# synthesis calls ranging from 6s to 264s depending on whether the 9.1GB
# model was still paged into RAM. 150s clipped 2 of 5 real calls outright.
# 280s gives real headroom for the slow case without being infinite.
# 2026-09-19 (Isaac, leaving the box unattended, live users hitting
# Generate Report): cut from 280.0. The 460s worst-case chain below was
# proven live (M2784/TSP0008 both took 379-387s to fall back tonight) --
# unacceptable when someone hits Generate Report and waits. The historical
# 280s reasoning above is still real (legitimate slow calls exist), but
# tonight's priority is bounded latency over squeezing every last
# legitimate AI success out of the slowest model. On timeout the pipeline
# doesn't fabricate -- it falls back to the safe, grammar-uncleaned raw
# remark (see rewrite()'s fallback path), so shortening this trades a
# little polish for a lot of predictability. Revisit upward once the
# escalation-trigger rate itself gets root-caused and fixed.
_TIMEOUT_SINGLE = 60.0
_TIMEOUT_BATCH  = 60.0
# Shorter per-call budget used for every escalation attempt EXCEPT the
# last one (see rewrite()'s escalation loop). If a fast/risky model is
# going to hang instead of failing fast, we'd rather cut our losses and
# try the next model than burn the full 280s on an attempt we might reject
# anyway. Only the final, nowhere-left-to-escalate-to attempt gets the
# full _TIMEOUT_SINGLE patience.
# try the next model than burn the full 280s on an attempt we might reject
# anyway. Only the final, nowhere-left-to-escalate-to attempt gets the
# full _TIMEOUT_SINGLE patience.
#
# Cut 90.0 -> 30.0 same night/same reason as _TIMEOUT_SINGLE above: worst
# case with 3 models was 2*90 + 280 = 460s, observed live at 379-387s.
# New worst case: 2*30 + 60 = 120s. Non-final models are the ones MOST
# likely to get guard-rejected anyway (that's why they're not primary/
# last) so spending less time on them before escalating is the right
# trade during a live-user, walking-away window.
_ESCALATION_TIMEOUT = 30.0
_TEMPERATURE    = 0.1
# Ollama's default keep_alive is 5 minutes -- proven live, this unloads the
# 9.1GB phi4:14b from RAM during any real-world gap between passdown entries
# (which happen sporadically over an 8-12hr shift, not back-to-back), forcing
# a full ~150-250s reload-from-disk on the next call. "30m" keeps the model
# resident across realistic gaps between shift log entries without holding
# it in memory forever if the server sits idle overnight.
_KEEP_ALIVE     = "30m"

# Model priority list -- first available one wins. phi4-mini moved to the
# front (2026-09-16, Isaac's call): phi4:14b's wild latency (6-264s/call,
# sequential per-WO with no parallelization) was making even small reports
# take several minutes. Today's session added substantial deterministic
# safety guards (dedup, content-free filtering, part-noun/tense/direction-
# reversal checks, unwarranted-resolution detection) that catch mistakes
# regardless of which model produced the raw text, so the quality gap
# matters less now. Trying mini as primary to see real-world output.
#
# qwen3-4b evaluated 2026-09-17 as a candidate escalation-tier addition --
# fast (5-8s/call) and passed every guard-sensitive test case. REVERTED
# the same day after live production use: this box has only 31.8GB RAM,
# CPU-only inference (no GPU), and the existing 3-model chain already
# holds ~22GB resident during an escalation cascade (phi4-mini 4.2GB +
# phi4:14b 11GB + llama3.1:8b 6.5GB per `ollama ps`). Adding a 4th model
# pushed the box to 2.3GB free RAM and caused repeated 280s full timeouts
# on /lead/generate during real shift use -- confirmed via server.log,
# not guesswork. The _THINKING_MODELS support code (prompt/no_think +
# stop-sequence + <think> stripping) stays in ai_rewriter.py since it's
# correct and harmless when unused -- only the priority-list entry was
# reverted.
#
# 2026-09-17 (later same night) -- promoted to PRIMARY, replacing
# phi4-mini rather than adding alongside it (chain length stays at 3, no
# new RAM pressure). Root cause: a full week of real passdown data
# (9/14-9/17) run through phi4-mini surfaced systemic "helpful rewrite"
# behavior the prompt's Rule 5 could not stop -- fabricated template-leak
# garbage ("with serial number the asset"), banned-word violations
# ("Assist with 2 baler wires" -> "Performed 2 baler wires"), flowery
# substitution ("old motor" -> "its predecessor"), and outright dropped
# content (a whole "back into service" clause vanished for CR0003).
# User's explicit ask: grammar/typo correction only, same meaning, no
# rewriting. qwen3-4b showed noticeably better instruction-following on
# this exact class of remark during initial evaluation -- confirmed via
# a direct before/after re-run of the same real WOs (fabrication and
# flowery substitution gone, CR0003's dropped clause came back).
#
# REVERTED again same night, quickly, per Isaac: a live quick-check call
# right after the swap hung / got interrupted, and there wasn't time to
# root-cause it on the spot (still warm in `ollama ps` at the correct
# 6.6GB, so it's not obviously the num_ctx fix regressing -- more likely
# needs a longer real-world soak before trusting it as primary again).
# Back to phi4-mini as primary for now. qwen3-4b stays installed with its
# corrected 8192 num_ctx Modelfile (down from the original 40960, which
# caused a 23GB resident-RAM blowup -- keep that fix regardless of which
# model is primary). Next step when picking this back up: reproduce the
# hang deliberately (single direct call, watch `ollama ps` + timing)
# before re-promoting it, rather than swapping back under time pressure.
#
# 2026-09-19 -- ESCALATION DISABLED (llama3.1:8b + phi4:14b removed from
# the list entirely), not just re-timed. Root cause of a live incident
# right before Isaac stepped away: a couple of remarks made phi4-mini's
# output get guard-rejected, which triggered escalation, which loaded
# ALL THREE models simultaneously (phi4-mini ~4GB + llama3.1:8b ~6GB +
# phi4:14b ~11GB = ~19.5-22GB resident, confirmed via `wmic` on this
# 32GB-RAM CPU-only box). That pushed the whole MACHINE into swap/thrash
# -- not just Ollama, but plain `echo` in a shell took 6.8s and `ollama
# ps` itself stopped responding for minutes. This is the same failure
# mode documented earlier this same night (qwen3-4b's oversized num_ctx
# caused an isolated version of it) but it turns out the risk was never
# qwen3-4b-specific -- ANY escalation cascade through all 3 stock models
# can trigger it. Single-model-only guarantees at most ~4GB for AI
# inference, full stop, no cascade possible. Trade-off: remarks phi4-mini
# can't produce a guard-passing rewrite for now fall straight to the
# safe raw-remark fallback instead of getting a second/third attempt at
# real AI polish. Given tonight's priority (unattended production, zero
# risk of the box choking) that's the right call. Re-enabling escalation
# later needs a real fix first -- e.g. explicitly `ollama stop` a model
# before trying the next one in the chain, so at most one is ever
# resident, rather than letting them stack.
_MODEL_PRIORITY = ["phi4-mini"]
_MODEL          = "phi4-mini"  # updated at startup by _detect_best_model()

# All _MODEL_PRIORITY candidates actually installed in Ollama, in priority
# order (fastest/primary first). Populated by _detect_best_model() /
# check_ollama(). Used for escalation in rewrite(): if the primary model's
# output gets rejected by a safety guard, we retry with the next-better
# model here rather than immediately giving up to the bare deterministic
# fallback -- see _rewrite_with_model().
_AVAILABLE_MODELS: list[str] = []

# Models that emit a <think>...</think> reasoning block before their real
# answer (Qwen3 family and any future thinking-mode model added here).
# Two things fall out of membership in this set, both handled in
# _rewrite_with_model():
#   1. "/no_think" is appended to the prompt -- proven live, letting a 4B
#      Qwen3 think freely burns way past this pipeline's num_predict budget
#      (180-400 tokens, tuned for non-thinking models) before it ever
#      reaches an answer, and can rack up 270s+ of rambling even on trivial
#      inputs -- a real risk against _ESCALATION_TIMEOUT's 90s budget.
#   2. The bare "\n\n" stop sequence is dropped -- it collides with Qwen3's
#      own required "<think>\n\n</think>\n\n" formatting and truncates the
#      response immediately after the opening <think> tag, before any
#      answer is ever produced. Confirmed live: raw output was the literal
#      4-character string "<think>" and nothing else.
_THINKING_MODELS = {"qwen3-4b"}

# Strips a <think>...</think> block (and any surrounding whitespace) from
# raw model output before it reaches _is_garbage()/the cleanup+guard chain.
# No-op for every non-thinking model, so this is safe to apply unconditionally.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def _strip_think_block(text: str) -> str:
    return _THINK_BLOCK_RE.sub("", text or "").strip()


log = logging.getLogger(__name__)


def _pick_from_available(available_names: list[str]) -> Optional[str]:
    """Pick the first _MODEL_PRIORITY candidate installed in Ollama, and
    cache the FULL set of installed candidates (in priority order) into
    _AVAILABLE_MODELS for escalation use.

    Substring match, not exact match: Ollama tags list names like
    'phi4-mini:latest' or 'llama3.1:8b', and our priority list uses bare
    names like 'phi4-mini'. An earlier exact-match version of this check
    silently failed for any ':latest'-tagged model -- 'phi4-mini' was never
    installed with that literal name, so it always fell through to the next
    candidate in the list without anyone noticing (proven live 2026-09-16:
    priority list said phi4-mini first, but the running process had quietly
    selected llama3.1:8b instead).
    """
    global _AVAILABLE_MODELS
    _AVAILABLE_MODELS = [
        candidate for candidate in _MODEL_PRIORITY
        if any(candidate in name for name in available_names)
    ]
    return _AVAILABLE_MODELS[0] if _AVAILABLE_MODELS else None


def _detect_best_model() -> str:
    """Pick the best available Ollama model from priority list."""
    global _MODEL
    try:
        import httpx as _httpx
        r = _httpx.get(f"{_OLLAMA_BASE}/api/tags", timeout=5)
        names = [m["name"] for m in r.json().get("models", [])]
        picked = _pick_from_available(names)
        if picked:
            _MODEL = picked
            log.info("[ai_rewriter] Selected model: %s", _MODEL)
            return _MODEL
    except Exception:
        pass
    log.info("[ai_rewriter] Using default model: %s", _MODEL)
    return _MODEL

_detect_best_model()


_SYSTEM_PROMPT = """
Purpose
Rewrite technician remarks into concise, professional industrial maintenance passdown entries
suitable for engineering shift handoffs and CMMS history.
Improve readability only while preserving every technical detail exactly as documented.

Core Rules
1. Preserve every technical fact, finding, troubleshooting step, corrective action,
   verification step, recommendation, escalation, and outcome exactly as described.
2. Never invent, assume, infer, exaggerate, simplify, summarize, or remove information.
3. Improve grammar, spelling, punctuation, and readability only.
4. Preserve the original chronological sequence of events.
5. Fix grammar, spelling, word order, and punctuation only. Do NOT replace the
   technician's own words with different vocabulary, synonyms, or more
   "professional"-sounding phrasing. If they wrote "brush", output "brush" --
   never upgrade it to "component". If they wrote "wire", output "wire" --
   never change it to "electrical connection". If they wrote "bench", output
   "bench" -- never expand it into "facility's shop area". The goal is the
   technician's own words, lightly cleaned up -- not a rewritten version in
   different vocabulary.
5a. Concrete example of what NOT to do (a real, rejected output): given
    "paper feed got jammed up reset and recalibrated no issues", do NOT
    produce "The voidfill process encountered an issue with a stuck-up
    paper feeder, however, after resetting and reconfiguring settings to
    accommodate this hiccup in operation without encountering further
    complications." That output invents corporate filler ("hiccup",
    "accommodate"), silently changes "recalibrated" into a DIFFERENT action
    ("reconfiguring settings"), and is missing its own main clause. The
    correct output is close to the original: "The paper feed jammed. It was
    reset and recalibrated with no issues." Fix grammar and punctuation --
    stop there.

Technician Language
6. Remove all first-person language.
   I replaced -> Replaced | We adjusted -> Adjusted | I noticed -> Found | I checked -> Inspected
   We may need X -> X may be needed | We might have to -> It may be necessary to
6a. Never invent a subject noun to replace the removed pronoun. Do NOT write
    "Technician replaced", "The technician found", "Tech adjusted", "MST inspected",
    or "Associate noticed" -- these are just as wrong as leaving "I" or "We" in.
    Go straight to the action verb with no subject at all, exactly like the
    examples in rule 6 (Replaced / Adjusted / Found / Inspected).
    This applies everywhere in the output, not just the first sentence --
    a remark with several sentences must never introduce a human subject
    partway through either.
7. When a remark begins with "Assisted with" or "Assisting with", remove the participation
    wording and rewrite only the completed maintenance action.
   Never use: Assisted, Accomplished, Participated, Helped, Supported.
   Assisting with calibration of the Fill Head -> Fill Head calibrated.
   Assisted with deinstalling the old light -> Old flickering light deinstalled.
   Assisted with replacing the motor -> Motor replaced.
7a. VOID RULE: If the entire remark is only a vague participation phrase with no specific
    asset, component, action, finding, or technical detail whatsoever, output only a dash: -
    Examples of remarks that must be voided:
      "Assisting with the repair."
      "Assisted with the fix."
      "Helped with maintenance."
      "Supported the team."
      "Assisted with the work."
    These contain no technical content to preserve. Output only: -
8. Remove conversational filler: anyways, just, basically, had to, went ahead and, no worries.

Technical Terminology
9.  Preserve all asset names, component names, and technical terminology exactly as written.
9a. Never invent, assume, or guess the type or category of equipment being described
    (examples of forbidden guesses: vehicle, car, truck, forklift, robot, cart).
    Only use an equipment-type word if it appears in the technician's remark or in an
    "Equipment context" line provided with the input. If the equipment type is not
    stated anywhere, refer to it only as "the asset" or by its asset ID -- never guess.
10. Preserve all directions exactly: Left, Right, Upper, Lower, Front, Rear, Top, Bottom.
11. Preserve all measurements, work order numbers, fault codes, locations, identifiers,
    quantities, and equipment references exactly.
11a. Never invent a descriptive name or label for a bare part/component ID
     (e.g. M345, M507, X6). If the technician only gave the ID, keep only
     the ID in the output -- do not guess or add what kind of part it is
     (e.g. do not call M507 a "fill head", "motor", or "sensor" unless the
     technician's remark itself said so).
12. Preserve technician maintenance vocabulary whenever technically correct.
    Keep: Unjammed, Swapped, Power cycled, Recalibrated, Realigned, Deinstalled, Lubricated,
    Tightened, Pulled, Out of tape, Boxes getting stuck, Box crushing, Paper done, Sensor flickering.
    Never rewrite into: Jamming issue resolved / Replaced with a spare unit /
    Restart sequence initiated / Investigation revealed / Additional issues affecting asset performance.
13. Preserve maintenance acronyms exactly. Never expand:
    LOTO, VFD, PLC, HMI, PM, RTS, MST.

Multi-Remark Synthesis
13a. The input may contain several remarks separated by " | ". These are NOT
     independent notes -- they are sequential log entries for the SAME issue,
     written by one or more technicians as the work progressed over the shift.
13b. Weave every " | "-separated remark into ONE continuous narrative paragraph,
     in the order given. Never output them as a list, never separate them with
     semicolons, and never simply concatenate them with "|" left in place.
     You may use a plain sequencing word (Following, After, Once) ONLY when the
     order of events is already obvious from what was actually written --
     never invent a cause-and-effect relationship, a reason, or an outcome
     that isn't stated in the remarks themselves. If none of the individual
     remarks state whether an attempt succeeded, failed, or matched, the
     synthesized paragraph must also leave that outcome unstated. Adding a
     result that wasn't written down is the same violation as adding a part
     or a cause that wasn't written down.
13d. Multiple remarks on the same work order are sometimes different
     technicians describing the SAME single action or finding in their own
     words, not distinct sequential steps (this is common when one tech logs
     the work and a second tech assisted on it). If two remarks clearly refer
     to the same underlying action (e.g. "replaced a bad brush on motor 4"
     and "installed a new brush for the motor" are the same repair, not two
     repairs), state it ONCE using whichever remark is more specific --
     do not restate the same fact twice in different words just because two
     people logged it. Only keep both as separate statements when they
     describe genuinely different actions, findings, or steps.
13c. If only one remark is given, only rephrase the words actually present --
     fix grammar and tighten wording, nothing more. Do NOT invent additional
     troubleshooting steps, root causes, parts, or verification that were
     never stated, even if the remark is very short or feels incomplete.
     A one-sentence remark must produce a one-sentence passdown entry.
     Padding a short remark with fabricated detail to sound more complete
     is strictly forbidden -- it is worse than leaving it short.

Troubleshooting
14. Preserve the complete troubleshooting sequence in order:
    Finding -> Investigation -> Escalation (if applicable) -> Recommendation -> Diagnosis ->
    Corrective Action -> Testing -> Verification -> Final Status.
15. Preserve every observation separately from the corrective action whenever both exist.
16. Preserve the technician's troubleshooting narrative exactly. Do not rewrite it into an
    engineering report or shorten it.
17. Preserve root causes exactly. Never generalize or simplify technical findings.
18. Preserve all testing and verification: 30-box test, auto calibration, brake test,
    test run, production verification.
19. Preserve every escalation. If Technical Support, Engineering, Controls, OEM support,
    vendors, maintenance leadership, or another support group was contacted, consulted,
    requested, or involved, this MUST appear in the output.
20. Preserve every recommendation received from Technical Support, Engineering, Controls,
    OEMs, or vendors.
21. Preserve cause-and-effect relationships. Never remove statements explaining why the
    next troubleshooting step occurred. Use transition language (Following, After,
    As a result) to connect steps that were actually described -- never invent a
    cause, a next step, a part, or an escalation that the technician did not state.

Operational Status
22. Only state operational status when explicitly supported by the technician's remarks.
23. Never invent operational status or diagnostic conclusions.
24. Normalize operational wording to: Asset returned to service.
    Applies to: Asset operational / Asset is operational now / Asset fully operational /
    Equipment returned to normal operation.
25. Never leave incomplete phrases such as: Asset returned to full. / Asset returned to.
26. Always use "asset" when referring to the equipment unless "unit", "machine", or another
    term is part of the official equipment name.

Prohibited
33. Never generate filler phrases: No further details provided / Maintenance performed /
    Operational status unknown / Issue addressed / Investigation continued /
    Additional work completed / Corrective action taken.
34. Never remove maintenance history that could help future troubleshooting.
35. Never replace technician terminology with polished report language.
36. Never omit Technical Support, Engineering, Controls, OEM, vendor, or leadership involvement.

Final Validation
Before returning the final output, verify:
- Every technical detail has been preserved.
- Every troubleshooting step has been preserved.
- Every observation has been preserved.
- Every root cause has been preserved.
- Every Technical Support, Engineering, Controls, OEM, vendor, or leadership interaction
  has been preserved.
- Every recommendation from support personnel has been preserved.
- No technician terminology has been unnecessarily changed.
- No operational status has been invented.
- No maintenance history has been removed.
- No outcome, result, or conclusion appears in the output that wasn't stated
  in the original remark(s) -- including implied success or failure of an
  attempted fix.
- The final output uses the technician's own words, lightly cleaned up for
  grammar and readability -- it is NOT restyled into different vocabulary
  and does NOT need to "sound like" anything other than what was written.

Output only the rewritten note. Nothing else.
"""

_SINGLE_TEMPLATE = (
    "Rewrite the following technician remark(s) as a single, flowing professional"
    " engineering shift passdown entry. If more than one remark is separated by"
    " ' | ', they are sequential entries for the same issue -- synthesize them into"
    " ONE continuous narrative, not a list. Follow all rules exactly."
    " Output only the rewritten note. Do not include the asset ID anywhere in the output,"
    " not even mid-sentence -- the report already shows it separately.\n"
    "{context_block}"
    "---\n"
    "Original: {text}\n"
    "Passdown:"
)

# Known asset-ID prefixes -> (equipment type, is_a_vehicle).
# Grounds the model so it never has to *guess* what kind of machine an asset
# is -- that guessing is exactly what produced "vehicle" for an autofill
# (Ranpak/void-fill) machine. Extend this list whenever a new asset family
# shows up in the shop; it costs nothing and prevents a whole class of
# hallucinated equipment-type words.
_ASSET_TYPE_GLOSSARY: dict[str, tuple[str, bool]] = {
    "TAPER":    ("taping machine", False),
    "VOIDFILL": ("void-fill / autofill packaging machine", False),
    "AUTOFILL": ("void-fill / autofill packaging machine", False),
    "BALER":    ("baler", False),
    "ROBOPAC":  ("stretch wrapping machine", False),
    "TENNANT":  ("floor scrubber", False),
    "SCRUB":    ("floor scrubber", False),
    "TSP":      ("stand-up forklift", True),
    "RC":       ("reach truck", True),
    "CR":       ("reach truck", True),
    "PERCART":  ("personnel cart", True),
    "TRKGATE":  ("dock/truck gate -- stationary equipment, not a vehicle", False),
    "BLDG":     ("building/facility -- not a piece of mobile equipment", False),
}


_CONTEXT_LINE = (
    "Equipment context (background only, do not repeat this sentence or any"
    " equipment-category label in the output): {vehicle_note} Refer to it only"
    " as the asset, it, or whatever word the technician's own remark used.\n"
)


def _asset_type_context(asset_id: str) -> str:
    """Return an 'Equipment context' line for known asset prefixes, or ''.

    Deliberately says NOTHING about what kind of machine this is beyond
    vehicle-or-not -- the fancy equipment name (e.g. "void-fill / autofill
    packaging machine", set in _ASSET_TYPE_GLOSSARY purely for maintainer
    reference) is never injected into the prompt, because the model was
    caught parroting it verbatim into the output instead of just writing
    "the asset" like a real technician would. All we actually need to stop
    a hallucination is the yes/no vehicle fact.
    """
    if not asset_id:
        return ""
    upper = asset_id.upper()
    for prefix, (_equip_type, is_vehicle) in _ASSET_TYPE_GLOSSARY.items():
        if upper.startswith(prefix):
            vehicle_note = (
                "This asset is a ride-on vehicle."
                if is_vehicle else
                "This asset is NOT a vehicle, car, truck, or forklift -- never call it one."
            )
            return _CONTEXT_LINE.format(vehicle_note=vehicle_note)
    return (
        "Equipment context: equipment type for this asset is unknown -- do not guess"
        " or invent one (e.g. do not call it a vehicle, car, truck, or forklift).\n"
    )

# -- HTTP client ---------------------------------------------------------------
_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            base_url=_OLLAMA_BASE,
            timeout=httpx.Timeout(_TIMEOUT_BATCH, connect=5.0),
        )
    return _client


# -- Startup -------------------------------------------------------------------

def check_ollama() -> bool:
    _sep = "=" * 68
    try:
        resp = _get_client().get("/api/tags", timeout=5.0)
        resp.raise_for_status()
    except Exception as exc:
        print(f"\n{_sep}\n[AI REWRITER] OLLAMA NOT RUNNING -- reports use original remarks.\n{_sep}\n", flush=True)
        log.warning("[ai_rewriter] Ollama unreachable: %s", exc)
        return False

    models = [m.get("name", "") for m in resp.json().get("models", [])]

    # Re-run model selection now that we KNOW Ollama is reachable.
    # _detect_best_model() also runs once at import time, but that can fire
    # before Ollama has finished starting (e.g. fresh boot, before anyone's
    # logged in to trigger the Ollama startup shortcut) -- in that case
    # _MODEL is stuck on the hardcoded default forever, even if a perfectly
    # good model is installed and running by the time we get here. Since we
    # already have the live model list in hand, redo the selection for free.
    global _MODEL
    picked = _pick_from_available(models)
    if picked:
        _MODEL = picked

    if not any(_MODEL in m for m in models):
        tried = ", ".join(_MODEL_PRIORITY)
        print(f"\n{_sep}\n[AI REWRITER] No usable model installed (tried: {tried})"
              f" -- run: ollama pull {_MODEL_PRIORITY[-1]}\n{_sep}\n", flush=True)
        log.warning("[ai_rewriter] No priority model found. Installed: %s", models)
        return False

    print(f"[ai_rewriter] Selected model: {_MODEL}", flush=True)
    print(f"[ai_rewriter] Warming up '{_MODEL}'...", flush=True)
    try:
        _call_ollama("warmup", num_predict=10)
    except Exception:
        pass

    print(f"[ai_rewriter] Ready.", flush=True)
    return True


# -- Core ----------------------------------------------------------------------

def _call_ollama(prompt: str, timeout: float = _TIMEOUT_SINGLE,
                 num_predict: int = 150, stop: list[str] | None = None,
                 model: str | None = None) -> Optional[str]:
    """model=None uses the globally-selected _MODEL. Callers doing model
    escalation (see _rewrite_with_model / rewrite()) pass an explicit
    override so a retry with a different model doesn't require mutating
    global state."""
    if stop is None:
        stop = ["\n\n", "Note:", "Examples", "<Your", "<your"]
    t0 = time.perf_counter()
    try:
        resp = _get_client().post(
            "/api/generate",
            json={
                "model":  model or _MODEL,
                "system": _SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
                "keep_alive": _KEEP_ALIVE,
                "options": {"temperature": _TEMPERATURE, "num_predict": num_predict, "stop": stop},
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        result = resp.json().get("response", "").strip()
        log.info("[ai_rewriter] %.2fs", time.perf_counter() - t0)
        return result or None
    except httpx.TimeoutException:
        log.warning("[ai_rewriter] Timeout after %.0fs", timeout)
        return None
    except Exception as exc:
        log.warning("[ai_rewriter] Error: %s", exc)
        return None


def _strip_leading_asset_id(text: str, asset_id: str) -> str:
    """Safety net: remove asset ID anywhere it slipped into the output.

    Handles both a leading prefix ("ASSETID: ...") and the model restating
    the ID mid-sentence ("During troubleshooting of ASSETID, ...") -- the
    report already renders the asset ID separately, so any occurrence here
    is a duplicate. A leading occurrence is dropped outright; a mid-sentence
    occurrence is swapped for "the asset" so the sentence stays grammatical.
    """
    if not asset_id:
        return text
    # Leading prefix: "ASSETID" / "ASSETID:" / "ASSETID -" / "ASSETID /"
    text = re.sub(rf"^{re.escape(asset_id)}\s*[-:/]?\s*", "", text, flags=re.IGNORECASE)
    # Any remaining mid-sentence mention -> "the asset" (keeps grammar intact)
    text = re.sub(rf"\b{re.escape(asset_id)}\b", "the asset", text, flags=re.IGNORECASE)
    # Collapse an accidental "the the asset"
    text = re.sub(r"(?i)\bthe\s+the asset\b", "the asset", text)
    text = re.sub(r"  +", " ", text).strip()
    return text


# Component-type words the model reliably invents to label a bare part/motor
# ID (e.g. turning "M345" into "fill head M345") based on general domain
# knowledge rather than anything the technician actually said. Prompt-level
# instructions (rule 11a) did NOT reliably stop this on live testing --
# the model repeated the same invented label 3/3 runs -- so this is a
# deterministic code-level scrubber instead.
_INVENTED_COMPONENT_LABELS = [
    "fill head", "photo eye", "motor", "actuator", "bearing", "roller",
]


def _strip_invented_component_labels(output: str, original: str) -> str:
    """Remove a descriptive component label the model attached to a bare
    part ID, when the technician's own remark never used that label.
    """
    orig_low = original.lower()
    for label in _INVENTED_COMPONENT_LABELS:
        if label in orig_low:
            continue  # technician actually said this -- leave it alone
        pattern = re.compile(
            rf"\b{re.escape(label)}s?\s*(?:component)?s?,?\s*"
            rf"(?=[A-Za-z]?\d{{2,5}}\b|\band\b)",
            re.IGNORECASE,
        )
        output = pattern.sub("", output)
    # Clean up a stray comma left behind where the label used to sit
    output = re.sub(r",\s+(were|was)\b", r" \1", output)
    output = re.sub(r"  +", " ", output).strip()
    return output


# Equipment-type words the model still invents for assets outside the
# glossary (proven live: an M-prefixed conveyor/machine got called "our
# heavy-duty vehicle" despite the prompt explicitly saying not to guess).
# Same story as the component-label scrubber above -- prompt rule 9a alone
# does not reliably stop this, so back it with a deterministic net.
_INVENTED_EQUIPMENT_TYPE_WORDS = ["vehicle", "car", "truck", "forklift"]


def _asset_is_confirmed_vehicle(asset_id: str) -> bool:
    """True only if the asset's known prefix is flagged as a ride-on vehicle
    in _ASSET_TYPE_GLOSSARY. Unknown prefixes are NOT vehicles by default --
    that default is exactly what stops the hallucination."""
    if not asset_id:
        return False
    upper = asset_id.upper()
    for prefix, (_equip_type, is_vehicle) in _ASSET_TYPE_GLOSSARY.items():
        if upper.startswith(prefix):
            return is_vehicle
    return False


def _strip_invented_equipment_type(output: str, asset_id: str, original: str) -> str:
    """Remove a vehicle/car/truck/forklift label the model invented for an
    asset that is not a confirmed ride-on vehicle, unless the technician's
    own remark used that word.
    """
    if _asset_is_confirmed_vehicle(asset_id):
        return output  # legitimately a vehicle -- leave it alone
    orig_low = original.lower()
    for word in _INVENTED_EQUIPMENT_TYPE_WORDS:
        if word in orig_low:
            continue  # technician actually said this -- leave it alone
        pattern = re.compile(
            rf"\b(?:our|the|this|a|an)?\s*(?:heavy-duty\s+|ride-on\s+)?{re.escape(word)}s?\b",
            re.IGNORECASE,
        )
        output = pattern.sub("the asset", output)
    output = re.sub(r"(?i)\bthe\s+the asset\b", "the asset", output)
    output = re.sub(r"  +", " ", output).strip()
    return output


# The model sometimes hedges on equipment identity by calling the asset "an
# unidentified/unspecified piece of equipment" instead of just using "the
# asset" like rule 26 asks for -- proven live (M2355 coolant remark). It's
# not a factual error so much as needless, confusing vagueness that makes
# the report read further from what the technician actually wrote.
_VAGUE_EQUIPMENT_PHRASE = re.compile(
    r"\b(?:an?\s+)?(?:unidentified|unspecified|unknown)\s+piece\s+of\s+(?:equipment|machinery)\b",
    re.IGNORECASE,
)


def _strip_vague_equipment_phrase(output: str) -> str:
    return _VAGUE_EQUIPMENT_PHRASE.sub("the asset", output)


# Ground-truth finding 2026-09-16: for CONFIRMED vehicle assets (TSP/RC/CR/
# PERCART), _asset_type_context() injects "This asset is a ride-on
# vehicle." as background-only context with an explicit instruction never
# to repeat that sentence or any equipment-category label. phi4-mini
# ignores that instruction and weaves the literal clinical phrase
# "ride-on vehicle" into its own sentence anyway (real examples: CR0003 ->
# "Unlocked this ride-on vehicle..."; TSP0002 -> "...for this ride-on
# vehicle..."). _strip_invented_equipment_type() deliberately leaves
# confirmed vehicles alone since calling them a vehicle isn't factually
# WRONG -- but "ride-on vehicle" is prompt jargon no technician would ever
# type, so it must never survive into a report regardless of whether the
# asset really is a vehicle. Strip unconditionally unless the technician's
# own remark used that exact phrase (vanishingly unlikely).
_RIDE_ON_VEHICLE_LEAK_RE = re.compile(
    r"\b(?:this|the|our)?\s*ride-on\s+vehicle\b", re.IGNORECASE
)


def _strip_ride_on_vehicle_leak(output: str, original: str) -> str:
    if "ride-on vehicle" in original.lower():
        return output
    output = _RIDE_ON_VEHICLE_LEAK_RE.sub("the asset", output)
    output = re.sub(r"(?i)\bthe\s+the asset\b", "the asset", output)
    return re.sub(r"  +", " ", output).strip()


# Proven live and severe: an early build's "Equipment context (background
# only): ..." preamble (meant purely as private instructions to the model,
# never shown to anyone) got parroted back nearly verbatim and shipped in a
# REAL shift passdown as if it were a second technician sub-bullet. Prompt
# wording alone ("do not repeat this sentence") is not a reliable enough
# guard on a small local model -- same lesson as every other scrubber in
# this file. "Equipment context" is specialized prompt-engineering jargon
# no technician would ever type, so treating it as a hard stop marker and
# truncating everything from that point on is safe.
_LEAKED_CONTEXT_LINE = re.compile(r"\s*Equipment context\b.*$", re.IGNORECASE | re.DOTALL)


def _strip_leaked_context_line(output: str) -> str:
    return _LEAKED_CONTEXT_LINE.sub("", output).strip()


# The single most dangerous fabrication class found in ground-truth testing:
# the model turned an explicitly UNRESOLVED problem into a fully-resolved,
# verified one. Proven live: "Unable to complete the test due to bad load
# wheels" (CR0007) became "...wheels were worn and required replacement.
# They were swapped with new ones, and a 30-box test was performed to
# verify proper operation." -- a complete, invented fix-and-verify story
# for a problem the technician said was still open. Rule 22/23 (never
# invent operational status/diagnostic conclusions) exists specifically
# for this. Token-diffing can't catch it (nothing was dropped, content was
# ADDED), so this is a dedicated contradiction check: if the input signals
# the work did NOT get completed, the output must preserve that signal too.
_INCOMPLETE_SIGNAL = re.compile(
    r"\b(?:unable to|could not|couldn't|was not able to|were not able to|"
    r"failed to|did not complete|didn't complete|not completed)\b",
    re.IGNORECASE,
)


def _claims_unwarranted_resolution(original: str, output: str) -> bool:
    """True if the technician flagged the work as incomplete/failed but the
    AI's rewrite dropped that signal -- i.e. invented a resolution."""
    return bool(_INCOMPLETE_SIGNAL.search(original)) and not _INCOMPLETE_SIGNAL.search(output)


# Ground-truth finding 2026-09-17: the model sometimes reinterprets a
# shop-specific physical-part term through a generic word's OTHER,
# unrelated meaning -- proven live: TSP0008's "info link" (this shop's own
# term for a physical link/tag on the unit) got rewritten as "information
# hyperlink," treating "link" as a web/software term instead of the
# mechanical part the technician meant. This is a narrower, more dangerous
# cousin of _dropped_part_nouns: instead of dropping detail, it invents a
# WRONG kind of detail dressed up as more specific-sounding. Since this
# shop repairs physical equipment, not IT systems, any of these
# web/software-only words appearing in an output where the technician never
# used them is a reliable signal something got reinterpreted through the
# wrong domain entirely. Deliberately does NOT include bare "link" --
# that's legitimate shop jargon ("info link") that must survive untouched.
_WRONG_DOMAIN_WORDS = (
    "hyperlink", "webpage", "web page", "website", "web site", " url ",
    "browser", "web link", "internet link", "software update",
)


def _uses_wrong_domain_vocabulary(original: str, output: str) -> bool:
    orig_low = f" {original.lower()} "
    out_low = f" {output.lower()} "
    return any(word in out_low and word not in orig_low for word in _WRONG_DOMAIN_WORDS)


# Ground-truth finding 2026-09-17: given clear, simple technician text
# ("paper feed got jammed up reset and recalibrated no issues"), the model
# paraphrased it into vague corporate filler AND changed the technical
# meaning in the process: "The voidfill process encountered an issue with
# a stuck-up paper feeder, however, after resetting and reconfiguring
# settings to accommodate this hiccup in operation without encountering
# further complications." -- "recalibrated" (a specific, correct action)
# became "reconfiguring settings" (a different action), and the sentence
# lost its main clause entirely (a grammatical fragment). Rule 5 already
# tells the model not to do this in prose, but small local models don't
# reliably follow prose instructions under pressure to "sound polished" --
# same reasoning as _uses_wrong_domain_vocabulary above. This is a
# deterministic backstop: reject output that introduces vague corporate
# padding vocabulary the technician never used. Matches the user's explicit
# ask (2026-09-17): grammar/clarity polish only, never new words, never
# meaning drift.
_FLOWERY_PADDING_WORDS = (
    "hiccup", "accommodate", "endeavor", "endeavour", "utilize", "utilise",
    "leverage", "facilitate", "in operation", "further complications",
    "encountered an issue", "reconfigur",  # catches reconfigure/-ing/-ed
    "stuck-up", "resultant", "myriad", "robust solution",
)


def _uses_flowery_padding_vocabulary(original: str, output: str) -> bool:
    orig_low = f" {original.lower()} "
    out_low = f" {output.lower()} "
    return any(word in out_low and word not in orig_low for word in _FLOWERY_PADDING_WORDS)


# Rule 6/6a say never insert a human-subject noun in place of the removed
# "I"/"We" -- but the model doesn't always comply (this is the exact bug
# reported live: "Technician adjusted...", "Technician observed..."). This
# is a deterministic safety net, same pattern as the scrubber above: catch
# it at the start of the output OR right after any sentence boundary, not
# just the very first word.
_INVENTED_SUBJECT_PATTERN = re.compile(
    r"(?:^|(?<=[.!?]\s))(?:the\s+)?(?:technician|tech|mst|associate)\s+(\w+)",
    re.IGNORECASE,
)


def _strip_technician_subject(text: str) -> str:
    """Remove an invented "Technician/Tech/MST/Associate <verb>" subject
    and go straight to the verb, matching the subject-free style the
    prompt already asks for everywhere else (rule 6 examples).
    """
    return _INVENTED_SUBJECT_PATTERN.sub(lambda m: m.group(1).capitalize(), text)


def _clean_output(text: str) -> str:
    """Strip informal phrases and fix formatting the model sometimes adds."""
    import re as _re
    # Void bare participation phrases with no technical content.
    # Anchored to full string -- specific detail after the noun keeps it alive.
    _VOID_PHRASES = _re.compile(
        r"^(?:assist(?:ed|ing)?|help(?:ed|ing)?|support(?:ed|ing)?|participat(?:ed|ing)?|perform(?:ed|ing)?)"
        r"\s+(?:with\s+)?(?:the\s+)?"
        r"(?:repair|fix|work|maintenance|task|job|effort|project|team)"
        r"\.?$",
        _re.IGNORECASE,
    )
    if _VOID_PHRASES.match(text.strip()):
        return "-"
    # Fix ALL CAPS output -- convert to sentence case
    if text == text.upper() and len(text) > 20:
        text = text.capitalize()
    text = _normalize_assisted_with(text)
    text = _fix_leading_tense(text)
    for pattern, replacement in _INFORMAL:
        text = _re.sub(pattern, replacement, text, flags=_re.IGNORECASE)
    text = _re.sub(r"\.\s*\.", ".", text)      # double periods
    text = _re.sub(r"  +", " ", text)           # double spaces
    text = _re.sub(r"\s*--\s*$", ".", text)    # trailing --
    text = _re.sub(r" @ ", " at ", text)        # @ -> at
    text = _re.sub(r"\s*\|\s*", "; ", text)    # | in output -> ;
    text = _re.sub(r";\s+", ", ", text)         # semicolons -> commas
    # Ensure sentence ends with period
    if text and text[-1] not in ".!?":
        text += "."
    return text.strip()


def _is_garbage(text: str) -> bool:
    low = text.lower()
    stripped = text.strip()
    # A bare dash is the AI's deliberate "nothing worth reporting" signal
    # (see _rewrite_with_model()) -- NOT garbage, even though it's short.
    # Bug found 2026-09-16: the len(text) < 10 check below always caught
    # this first, so the dedicated dash-handling code further downstream
    # was unreachable dead code -- a bare "-" from the AI always looked
    # like garbage and triggered escalation/fallback instead of being
    # honored as "the technician's remark had nothing worth adding".
    if stripped in ("-", "--"):
        return False
    # A stray space directly before the final period ("...its standard .")
    # means the model got cut off mid-word/mid-phrase, usually because
    # num_predict or a stop sequence fired early -- proven live on a servo
    # calibration remark. Treat it as garbage so the fallback kicks in
    # instead of shipping a visibly broken sentence. Also catch the
    # no-dangling-space flavor of the same bug ("...was returned to.") --
    # ending on a bare preposition/article is grammatically incomplete
    # regardless of whether there's a space before the period.
    truncated = bool(re.search(r"\s[.!?]$", text.strip()))
    dangling_word = bool(re.search(
        r"\b(?:to|for|with|on|of|in|and|the|a|an)\.$", text.strip(), re.IGNORECASE
    ))
    return truncated or dangling_word or len(text) < 10 or any(g in low for g in (
        "<", "your answer", "your response", "[insert", "[your", "n/a"
    ))


# Zero-AI first-person removal, applied on EVERY output path -- not just
# fallback. Ground-truth testing against the real production DB (Friday
# 9/11 S5 shift, dbs/ind3.db) proved the model invents "we're"/"we've"/
# "I found" narration from scratch on SUCCESSFUL calls fed already-clean,
# already-impersonal raw remarks (e.g. "Both M504 and M507 PEs were found
# to be misaligned" -> "I found that only one was misaligned"). This isn't
# a fallback-only problem -- rule 6 was being violated on the happy path
# too, silently, because nothing downstream of the AI call ever re-checked
# for it. The `(?!-)` guard keeps this from mangling compound terms like
# "I-beam".
_FIRST_PERSON_WORD = re.compile(r"\b(?:i|we)(?:'m|'re|'ve|'d|'ll)?\b(?!-)\s*", re.IGNORECASE)


def _strip_first_person(text: str) -> str:
    if not text:
        return text
    text = _FIRST_PERSON_WORD.sub("", text)
    text = re.sub(r"\s+([,.;])", r"\1", text)   # no space before punctuation
    text = re.sub(r"  +", " ", text).strip()
    if text:
        text = text[0].upper() + text[1:]
    # Re-capitalize the start of every sentence after words got dropped.
    text = re.sub(r"([.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), text)
    return text


# Ground truth also proved the model silently DROPS specific identifiers
# the technician gave it -- a physical destination ("moved it to 6-2") just
# vanished from a real report, replaced with vague filler ("relocated for
# accurate processing"). Numbers, part IDs, and location codes are exactly
# the details a shift lead needs and can't reconstruct from memory, so
# losing one silently is worse than a slightly clunkier sentence. This is a
# cheap, deterministic safety net: if any digit-bearing token from the
# input (M504, 6-2, 4.5, WO numbers, ...) is missing from the AI's output,
# don't trust the AI's rewrite for this call at all -- use the deterministic
# fallback instead, which by construction can't drop a token that was there.
_NUMERIC_TOKEN = re.compile(r"\b[A-Za-z]{0,4}\d[\w.-]*\b")


def _numeric_tokens(text: str) -> set[str]:
    return {t.lower() for t in _NUMERIC_TOKEN.findall(text)}


# Ground-truth finding 2026-09-16: the prompt explicitly says "Do not
# include the asset ID anywhere in the output, not even mid-sentence" --
# but a no-separator asset ID like VOIDFILL0801 can get misread by a small
# model as ordinary English words plus a number rather than an opaque
# identifier. Real example: asset VOIDFILL0801 with remark "Taped changed
# and spacer added..." -> phi4-mini produced "Has voided fill 0801 on this
# machine...", inventing a nonsensical action out of the asset's own ID.
# General, low-false-positive check: if the asset ID's own trailing digit
# run appears in the output but that same digit run is NOT anywhere in the
# technician's original remark, the model can only have pulled it from the
# asset ID itself -- a real measurement the technician wrote would, by
# definition, already be present in the original body text.
def _leaked_asset_id_digits(output: str, asset_id: str, original_body: str) -> bool:
    m = re.search(r"(\d{2,})$", asset_id or "")
    if not m:
        return False
    suffix = m.group(1)
    if re.search(rf"\b{re.escape(suffix)}\b", original_body):
        return False  # technician's own remark legitimately used this number
    return bool(re.search(rf"\b{re.escape(suffix)}\b", output))


def _dropped_key_tokens(original_body: str, output: str, asset_id: str = "") -> set[str]:
    """Numeric/identifier tokens present in the input but missing from the
    output -- a reliable proxy for "the AI silently threw away a detail".

    Excludes the CURRENT asset's own ID (see `asset_id`). Ground-truth
    finding 2026-09-17: a remark that references its own asset ID
    mid-sentence (e.g. TSP0008's own remark saying "gasket taken off from
    TSP0008 to be used on TSP0002") is CORRECTLY and intentionally stripped
    of that self-reference by _strip_leading_asset_id() -- the report
    already shows "TSP0008:" as the bullet's own label, so repeating it
    inline is redundant, not informative. Without this exclusion, EVERY
    compliant rewrite of such a remark permanently fails this guard (since
    a well-behaved model correctly drops its own asset ID every time),
    forcing every escalation attempt to be rejected and landing on the
    zero-AI fallback even when a model did a perfectly good job. Other
    assets' IDs (like TSP0002 here) are NOT excluded and remain fully
    protected -- only the WO's own asset ID gets this pass.
    """
    dropped = _numeric_tokens(original_body) - _numeric_tokens(output)
    if asset_id:
        dropped.discard(asset_id.lower())
    return dropped


# Specific technical part/component nouns that must never be silently
# swapped for a vaguer generic word. Proven live: both llama3.1:8b and
# phi4:14b turned "trying the fittings on the new drive motor" into
# "fitting of components" -- "fittings" is a specific part name, and
# downgrading it to "components" changes what physical part is being
# discussed, which is exactly as dangerous as inventing or dropping a part
# number. Compared by stem so singular/plural rewording (wire -> wires)
# doesn't false-positive.
_PRESERVE_PART_NOUNS = {
    "fittings", "fitting", "brush", "brushes", "bearing", "bearings",
    "gasket", "gaskets", "valve", "valves", "coupler", "couplers",
    "coupling", "couplings", "hose", "hoses", "belt", "belts",
    "chain", "chains", "linkage", "wheel", "wheels", "fuse", "fuses",
    "sensor", "sensors", "servo", "servos", "wire", "wires", "wiring",
}
_FITTING_VERB_FORM_RE = re.compile(r"\bfitting\s+(?:of|the)\b", re.IGNORECASE)


def _stem(word: str) -> str:
    return word[:-1] if word.endswith("s") and len(word) > 3 else word


def _dropped_part_nouns(original_body: str, output: str) -> set[str]:
    """Specific part nouns present in the input but silently swapped for a
    vaguer generic word in the output (see _PRESERVE_PART_NOUNS above).

    False-friend guard: "fitting of components" is the verb/gerund sense
    (the ACT of fitting something) -- proven live, the model can use this
    phrasing while still swapping out the actual part noun ("fittings") for
    "components". A bare stem match would wrongly treat that as preserved,
    so strip that verb-form phrasing out of the output before comparing.
    """
    orig_stems = {_stem(w) for w in re.findall(r"[a-zA-Z]+", original_body.lower())}
    output_for_check = _FITTING_VERB_FORM_RE.sub(" ", output.lower())
    out_stems = {_stem(w) for w in re.findall(r"[a-zA-Z]+", output_for_check)}
    preserve_stems = {_stem(w) for w in _PRESERVE_PART_NOUNS}
    return (orig_stems & preserve_stems) - out_stems


# "new X" / "old Y" swap-direction reversal. Proven live: tech said fittings
# came FROM the old motor and were tried ON the new one (reusing old parts
# during a replacement) -- the AI sometimes flips this to FROM the new motor
# ONTO the old one, which is backwards and doesn't even make operational
# sense (a motor being removed/replaced wouldn't receive new parts). This is
# non-deterministic (varies run to run), so a fallback guard is the only
# reliable defense -- prompt wording can reduce it but can't guarantee it
# never happens. Conservative by design: only fires when both texts clearly
# attach "new" and "old" to an unambiguous source ("from") or destination
# ("on"/"onto"/"to"/"using"/"with") preposition -- anything murkier is left
# alone rather than risk a false positive.
_NEW_OLD_PREP_RE = re.compile(r"\b(on|onto|from|to|using|with)\s+(?:the\s+)?(new|old)\b", re.IGNORECASE)


def _new_old_direction_reversed(original: str, output: str) -> bool:
    def _prep_map(text: str) -> dict[str, set[str]]:
        m: dict[str, set[str]] = {}
        for prep, which in _NEW_OLD_PREP_RE.findall(text):
            m.setdefault(which.lower(), set()).add(prep.lower())
        return m

    def _role(preps: set[str]) -> str | None:
        has_from = "from" in preps
        has_dest = bool(preps - {"from"})
        if has_from and not has_dest:
            return "source"
        if has_dest and not has_from:
            return "dest"
        return None

    orig_map, out_map = _prep_map(original), _prep_map(output)
    if "new" not in orig_map or "old" not in orig_map:
        return False
    if "new" not in out_map or "old" not in out_map:
        return False
    orig_new, orig_old = _role(orig_map["new"]), _role(orig_map["old"])
    out_new, out_old = _role(out_map["new"]), _role(out_map["old"])
    if None in (orig_new, orig_old, out_new, out_old):
        return False
    return orig_new != out_new and orig_old != out_old


# Common informal/casual phrases the model slips in -- replace with nothing
_INFORMAL = [
    (r"all systems go!?",              ""),
    (r"back in business\.?",           "asset returned to service."),
    (r"good to go!?",                  "asset returned to service."),
    (r"ready to roll!?",               "asset returned to service."),
    (r"up and running",                "returned to service"),
    (r"making it functional again\.?", "asset returned to service."),
    (r"(?i)\ball systems(?:\s+are)?\s+go\b", ""),
    (r"as usual\.?",                  ""),
    (r"no worries\.?",                ""),
    # AI-invented filler when no outcome was stated -- strip entirely
    (r"no further details? are available[^.]*\.",           ""),
    (r"the (?:current |operational )?status (?:of the (?:unit|asset) )?(?:remains?|is) (?:unknown|unclear|not specified|unavailable)[^.]*\.", ""),
    (r"(?:regarding |concerning )?(?:its |the )?(?:(?:unit|asset).s |)?(?:current )?operational (?:status|functionality)[^.]*\.", ""),
    (r"further (?:details?|information) (?:regarding|about|on)[^.]*(?:status|condition|functionality)[^.]*\.", ""),
]

# Warehouse-specific typo map -- applied BEFORE sending to AI.
# Pattern -> replacement (case-insensitive word boundary match)
_TYPOS: list[tuple[str, str]] = [
    (r"\bcraahing\b",       "crashing"),
    (r"\bcraashing\b",      "crashing"),
    (r"\brecalibreted\b",   "recalibrated"),
    (r"\brecalibrated\b",   "recalibrated"),   # already correct, no-op
    (r"\boperationl\b",    "operational"),
    (r"\boperatinal\b",    "operational"),
    (r"\bcloser(?!s)\b",   "closer"),          # no-op placeholder for future
    (r"\bblushings?\b",    "blushing"),         # normalize plural
]


def _fix_typos(text: str) -> str:
    """Fix known warehouse-specific typos before sending to the AI."""
    import re as _re
    for pattern, replacement in _TYPOS:
        text = _re.sub(pattern, replacement, text, flags=_re.IGNORECASE)
    return text


# Rule 7: "Assisted/Assisting with X" -> direct action verb. Purely
# mechanical, zero fabrication risk -- so unlike the rest of _INFORMAL this
# needs to run on EVERY path, not just the AI-success path. Proven live:
# a short remark ("Assisted with installing new brushes on the flex") hit
# the <=8-word bypass and shipped with "Assisted with" still in it, because
# _simple_cleanup() never called this.
_ASSISTED_WITH_PATTERNS = [
    (r"(?i)\bAssist(?:ed|ing)? with adjusting\b",   "Adjusted"),
    (r"(?i)\bAssist(?:ed|ing)? with replacing\b",   "Replaced"),
    (r"(?i)\bAssist(?:ed|ing)? with installing\b",  "Installed"),
    (r"(?i)\bAssist(?:ed|ing)? with calibrating\b", "Calibrated"),
    (r"(?i)\bAssist(?:ed|ing)? with deinstalling\b","Deinstalled"),
    (r"(?i)\bAssist(?:ed|ing)? with reinstalling\b","Reinstalled"),
    (r"(?i)\bAssist(?:ed|ing)? with removing\b",    "Removed"),
    (r"(?i)^Please assist me (?:in|with)\s+", ""),
    (r"(?i)\bAssist(?:ed|ing)? with\b",             "Performed"),
    (r"(?i)\bAssisting\b",                          "Performing"),
]


def _normalize_assisted_with(text: str) -> str:
    for pattern, replacement in _ASSISTED_WITH_PATTERNS:
        text = re.sub(pattern, replacement, text)
    return text


# Rule 8: whole-shift passdowns describe work that ALREADY happened -- a
# remark opening with a present-tense/imperative verb ("Take meter reading",
# checklist-style phrasing many techs default to) or a gerund ("Performing
# the function test") reads as an instruction or an ongoing action, not a
# completed one. Proven live: real remarks "Take meter reading on all Fork
# Lifts" and "Performing the function test. Plugging:5 ft Braking:3 ft" both
# bypass the AI (short/checklist-style) and shipped verbatim, present tense
# and all. Purely mechanical lookup on the FIRST verb of each sentence only
# -- never touches verbs mid-sentence, where tense is far more ambiguous
# (e.g. "needs to check", conditional/future phrasing).
_LEADING_VERB_PAST_TENSE = {
    "take": "Took", "taking": "Took",
    "perform": "Performed", "performing": "Performed",
    "check": "Checked", "checking": "Checked",
    "clean": "Cleaned", "cleaning": "Cleaned",
    "install": "Installed", "installing": "Installed",
    "replace": "Replaced", "replacing": "Replaced",
    "inspect": "Inspected", "inspecting": "Inspected",
    "test": "Tested", "testing": "Tested",
    "adjust": "Adjusted", "adjusting": "Adjusted",
    "calibrate": "Calibrated", "calibrating": "Calibrated",
    "verify": "Verified", "verifying": "Verified",
    "confirm": "Confirmed", "confirming": "Confirmed",
    "tape": "Taped", "taping": "Taped",
    "monitor": "Monitored", "monitoring": "Monitored",
    "tighten": "Tightened", "tightening": "Tightened",
    "reboot": "Rebooted", "rebooting": "Rebooted",
    "lubricate": "Lubricated", "lubricating": "Lubricated",
    "grease": "Greased", "greasing": "Greased",
    "remove": "Removed", "removing": "Removed",
    "add": "Added", "adding": "Added",
    "torque": "Torqued", "torquing": "Torqued",
}
_LEADING_VERB_RE = re.compile(r"^([A-Za-z]+)\b")


def _fix_leading_tense(text: str) -> str:
    """Convert a present-tense/gerund verb at the START of each sentence to
    simple past -- see _LEADING_VERB_PAST_TENSE. Mid-sentence verbs are left
    alone entirely; only the leading word of each sentence is a safe enough
    signal that it's describing an action the technician took."""
    sentences = re.split(r"(?<=[.!?])\s+", text)
    fixed = []
    for s in sentences:
        m = _LEADING_VERB_RE.match(s)
        if m:
            past = _LEADING_VERB_PAST_TENSE.get(m.group(1).lower())
            if past:
                s = past + s[m.end(1):]
        fixed.append(s)
    return " ".join(fixed)


# -- Public API ----------------------------------------------------------------

# Small local models don't reliably notice when two remarks (usually from
# two different techs on the same WO) describe the SAME action in different
# words -- proven live, both llama3.1:8b and phi4:14b kept "replaced a bad
# brush on motor 4" and "installed a new brush for the motor" as two separate
# sentences instead of one. Doing this deterministically instead of via
# prompt instruction, with a conservative safety rail: never collapse two
# remarks unless the one being dropped adds ZERO new numeric/identifier
# tokens beyond what's kept -- losing a part number or quantity because of
# an overzealous dedup heuristic would be worse than a little redundancy.
_DEDUPE_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "with", "for", "on", "in", "at", "to", "of",
    "is", "was", "were", "be", "been", "being", "it", "its", "this", "that", "these", "those",
    "assisted", "assisting", "helped", "helping", "new", "old", "had", "have", "has",
}
_DEDUPE_OVERLAP_THRESHOLD = 0.6
_DEDUPE_MIN_SIGNIFICANT_WORDS = 2


def _significant_words(remark: str) -> set[str]:
    """Lowercase content words, minus stopwords and pure-number tokens
    (numbers are handled separately via _numeric_tokens for exact matching)."""
    words = re.findall(r"[a-zA-Z]+", remark.lower())
    return {w for w in words if w not in _DEDUPE_STOPWORDS and len(w) > 1}


# Generic activity/diagnostic words that name NO specific part, asset
# detail, or finding on their own. Proven live via real timecard data:
# "Assist" (bare, zero content) got logged on a WO that already had a real
# substantive remark from earlier the same shift -- it adds nothing and
# would otherwise get fed to the AI alongside the real remark. Distinction
# that matters (per Isaac): "Assisted with installing new brushes on the
# flex" is fine -- "brushes"/"flex" are concrete. "Assisted in
# troubleshooting" is not -- "troubleshooting" alone says nothing about
# what was actually found or done.
_GENERIC_ACTIVITY_WORDS = {
    "troubleshooting", "troubleshoot", "checking", "check", "diagnosing",
    "diagnose", "looking", "look", "working", "work", "monitoring",
    "monitor", "inspecting", "inspect", "reviewing", "review",
    "helping", "help", "assisting", "assist", "assisted",
    "remove", "removal", "removed", "removing",
    "perform", "performed", "performing",
}


def _is_content_free_remark(remark: str) -> bool:
    """True if nothing specific (part, asset detail, concrete action)
    survives after stripping filler/connector words and generic activity
    verbs -- see _GENERIC_ACTIVITY_WORDS."""
    words = [w for w in re.findall(r"[a-zA-Z]+", remark.lower()) if w not in _DEDUPE_STOPWORDS]
    if not words:
        return True
    return all(w in _GENERIC_ACTIVITY_WORDS for w in words)


def _drop_content_free_remarks(remarks: list[str]) -> list[str]:
    """Remove remarks carrying zero real information (see
    _is_content_free_remark). Safety rail: never wipe the list down to
    nothing -- a content-free remark is still better shown than an empty
    bullet, so if EVERY remark is content-free, leave them all alone."""
    filtered = [r for r in remarks if not _is_content_free_remark(r)]
    return filtered if filtered else remarks


def _concrete_words(remark: str) -> set[str]:
    """Significant words with generic activity/action verbs also stripped
    out -- leaves only the words that actually identify WHAT was worked on
    (parts, asset details), not just that some generic activity happened."""
    return _significant_words(remark) - _GENERIC_ACTIVITY_WORDS


def _dedupe_vague_subset_remarks(remarks: list[str]) -> list[str]:
    """Drop a remark if every concrete word it contains already appears in
    a longer remark for the same WO -- i.e. it adds no information beyond
    a generic-activity restatement. Proven live: real timecard remark
    "assist with removal of gasket" added nothing once "Gasket taken off
    from TSP0008 to be used on TSP0002..." was already present for the same
    WO -- _dedupe_same_event_remarks' overlap-ratio check missed this
    because the vague remark shares only one word ("gasket") out of three,
    which falls under the ratio threshold even though that one word is a
    FULL match of everything concrete the vague remark says.

    Conservative by design: only collapses on a full subset match (every
    concrete word already present elsewhere) and requires at least one
    concrete word to match on -- an empty core would trivially "match"
    everything and must never trigger a collapse. Also never collapses if
    the shorter remark carries a numeric/identifier token missing from the
    longer one.
    """
    keep = [True] * len(remarks)
    for i, remark in enumerate(remarks):
        core = _concrete_words(remark)
        if not core:
            continue
        nums = _numeric_tokens(remark)
        for j, other in enumerate(remarks):
            if i == j or not keep[j]:
                continue
            other_words = _significant_words(other)
            other_nums  = _numeric_tokens(other)
            if nums - other_nums:
                continue
            if core <= other_words and len(other_words) > len(_significant_words(remark)):
                keep[i] = False
                break
    filtered = [r for r, k in zip(remarks, keep) if k]
    return filtered if filtered else remarks


def _dedupe_same_event_remarks(remarks: list[str]) -> list[str]:
    """Collapse remarks that clearly describe the same single action/event
    (commonly: two techs logging the same repair in their own words) down
    to one -- keeping whichever carries more content. Conservative by
    design: only collapses on high word overlap, and never if doing so
    would drop a numeric/identifier token that isn't preserved elsewhere.
    """
    kept: list[str] = []
    for remark in remarks:
        words = _significant_words(remark)
        nums  = _numeric_tokens(remark)
        collapsed = False
        if len(words) >= _DEDUPE_MIN_SIGNIFICANT_WORDS:
            for i, existing in enumerate(kept):
                existing_words = _significant_words(existing)
                existing_nums  = _numeric_tokens(existing)
                if len(existing_words) < _DEDUPE_MIN_SIGNIFICANT_WORDS:
                    continue
                # Both sides mention numbers but they differ -> different
                # specific items (different motor #, different part #) --
                # never treat as the same event.
                if nums and existing_nums and nums != existing_nums:
                    continue
                overlap = len(words & existing_words) / min(len(words), len(existing_words))
                if overlap < _DEDUPE_OVERLAP_THRESHOLD:
                    continue
                # Safety rail: only collapse if this remark introduces no
                # numeric/identifier token the kept one lacks.
                if nums - existing_nums:
                    continue
                if len(words) > len(existing_words):
                    kept[i] = remark
                collapsed = True
                break
        if not collapsed:
            kept.append(remark)
    return kept


def build_input(asset_id: str, remarks: list[str]) -> str:
    """Build the canonical input string for rewrite / rewrite_batch.

    Applies Python-level typo fixes before sending to the AI -- more
    reliable than depending on the model to catch them every time.
    Drops content-free remarks (see _drop_content_free_remarks), drops
    vague remarks that are a full subset of a more detailed remark for the
    same WO (see _dedupe_vague_subset_remarks), and collapses same-event
    duplicate remarks (see _dedupe_same_event_remarks) before the AI ever
    sees them, since neither llama3.1:8b nor phi4:14b reliably follow a
    prompt instruction to do this themselves -- proven live, both produced
    near-identical redundant two-sentence output for "replaced a bad brush
    on motor 4" + "installed a new brush for the motor".
    Format: "ASSET_ID / remark1 | remark2 | ..."
    """
    fixed = [_fix_leading_tense(_normalize_assisted_with(_fix_typos(r.strip()))) for r in remarks if r.strip()]
    fixed = _drop_content_free_remarks(fixed)
    fixed = _dedupe_vague_subset_remarks(fixed)
    fixed = _dedupe_same_event_remarks(fixed)
    body  = " | ".join(fixed)
    if not asset_id:
        return body  # sub-bullet inputs: no prefix, just the remark
    return f"{asset_id} / {body}"


# Local models reliably "pad" very short single remarks with fabricated detail
# to sound more complete (proven live: "Load paper" -> invented paper jams,
# "Misalignment of conveyor sensor." -> invented Technical Support escalations
# that never happened). Below this word count, for a single remark, skip the
# AI entirely and use deterministic cleanup -- there's no safe way to prompt
# a small local model out of this behavior, so don't give it the chance.
_MIN_WORDS_FOR_AI = 8


def _log_correction(original: str, corrected: str) -> None:
    """Change-audit trail: log exactly what the deterministic pipeline
    changed, so a lead can spot-check corrections without re-deriving them.
    Silent when nothing changed (the common case) to avoid log spam."""
    if original and corrected and original.strip() != corrected.strip():
        log.info("[deterministic-correct] %r -> %r", original, corrected)


def _simple_cleanup(text: str) -> str:
    """Deterministic, zero-fabrication cleanup for very short remarks."""
    original = text.strip()
    text = original
    if not text:
        return text
    text = _normalize_assisted_with(text)
    text = _fix_leading_tense(text)
    text = text_normalize.normalize(text)
    text = spelling_fix.correct_spelling(text)
    text = text[0].upper() + text[1:]
    if text[-1] not in ".!?":
        text += "."
    _log_correction(original, text)
    return text


# Real asset IDs in this shop are always a single token with no spaces
# (VOIDFILL0501, CR0010, TRKGATE0001, BLDG...). A remark that happens to
# contain a natural " / " ("belt / pulley assembly", "power supply / PLC")
# must NOT be mistaken for an "ASSETID / body" prefix -- that mistake
# silently deleted the entire first clause of a remark on live testing
# ("Swapped the belt / pulley assembly..." lost "Swapped the belt" outright).
_ASSET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,20}$")


def _looks_like_asset_id(candidate: str) -> bool:
    return bool(_ASSET_ID_PATTERN.match(candidate.strip()))


def _deterministic_fallback(text: str, asset_id: str) -> str:
    """Zero-AI cleanup used both when the AI call fails/times out AND when
    its output can't be trusted (dropped a key identifier). Never invents
    anything -- just de-personizes, spell-checks, and punctuates the
    technician's own words, since a plain-but-complete entry beats a
    polished-but-wrong one.

    2026-09-19: promoted from "failure fallback" to the PRIMARY correction
    path for every remark (see rewrite()) per Isaac's explicit final call:
    fix spelling/punctuation/grammar only, never rephrase, and the same
    input must always produce the same output -- a guarantee no generative
    model can make. Spelling correction added here via spelling_fix.py
    (plain dictionary lookup, not AI) to close the gap between "safe" and
    "actually looks proofread".
    """
    fallback = _strip_leading_asset_id(text, asset_id)
    fallback = re.sub(r"\s*\|\s*", "; ", fallback)
    fallback = _strip_first_person(fallback)
    fallback = _normalize_assisted_with(fallback)
    fallback = _fix_leading_tense(fallback)
    fallback = text_normalize.normalize(fallback)
    fallback = spelling_fix.correct_spelling(fallback)
    fallback = fallback.strip()
    if fallback:
        fallback = fallback[0].upper() + fallback[1:]
        if fallback[-1] not in ".!?":
            fallback += "."
    _log_correction(text, fallback)
    return fallback


def _rewrite_with_model(
    model: str, text: str, asset_id: str, body: str,
    context_block: str, num_predict: int,
    timeout: float = _TIMEOUT_SINGLE,
) -> Optional[str]:
    """Attempt one AI rewrite with a SPECIFIC model and run it through every
    safety guard. Returns the cleaned text on success, "" if the AI
    legitimately signalled "nothing worth reporting", or None if the model's
    output was garbage or tripped a guard -- callers use None to decide
    whether to escalate to the next model (see rewrite()).

    `timeout` defaults to the full _TIMEOUT_SINGLE (280s) but rewrite()
    passes a shorter budget for non-final escalation attempts -- see the
    comment on _ESCALATION_TIMEOUT there for why.
    """
    prompt = _SINGLE_TEMPLATE.format(text=text, context_block=context_block)
    stop = ["---", "\nOriginal:", "\nNote:", "\nExample", "\n\n"]
    if model in _THINKING_MODELS:
        # See _THINKING_MODELS' comment: thinking mode blows the num_predict
        # budget and "\n\n" collides with Qwen3's own <think> formatting.
        prompt += "\n/no_think"
        stop.remove("\n\n")
    result = _call_ollama(
        prompt,
        timeout=timeout,
        num_predict=num_predict,
        stop=stop,
        model=model,
    )
    result = _strip_think_block(result)
    if not result or _is_garbage(result):
        return None
    cleaned = _clean_output(_strip_leading_asset_id(result, asset_id))
    cleaned = _strip_leaked_context_line(cleaned)
    cleaned = _strip_invented_component_labels(cleaned, text)
    cleaned = _strip_invented_equipment_type(cleaned, asset_id, text)
    cleaned = _strip_ride_on_vehicle_leak(cleaned, text)
    cleaned = _strip_vague_equipment_phrase(cleaned)
    cleaned = _strip_technician_subject(cleaned)
    cleaned = _strip_first_person(cleaned)
    # AI signals "nothing worth reporting" with a bare dash -- legitimate
    # result, not a rejection, so this short-circuits before the guards.
    # Checked against the raw stripped punctuation, not the exact string,
    # since _clean_output() appends a trailing period to any short output
    # on its way through ("-" -> "-."), which an exact-match check missed.
    if cleaned.strip().rstrip(".!?") in ("-", "--", ""):
        return ""
    # The asset ID itself leaking into the output disguised as ordinary
    # words + a number (see _leaked_asset_id_digits()) -- check this before
    # the other content-comparison guards since a leaked ID makes the rest
    # of the sentence untrustworthy too.
    if _leaked_asset_id_digits(cleaned, asset_id, body):
        return None
    # Ground-truth check: did the AI silently drop a part ID, location code,
    # measurement, or WO number that was in the input? If so, don't ship a
    # polished-but-incomplete rewrite -- escalate/fall back instead.
    # (Proven live: "moved it to 6-2" -> the "6-2" vanished entirely.)
    if _dropped_key_tokens(body, cleaned, asset_id):
        return None
    # Same idea, but for specific part nouns silently swapped for a vaguer
    # generic word ("fittings" -> "components") -- see _dropped_part_nouns().
    if _dropped_part_nouns(body, cleaned):
        return None
    # "new X" / "old Y" swap-direction reversal guard -- see
    # _new_old_direction_reversed(). Non-deterministic failure mode, so this
    # is the only reliable defense against shipping a backwards statement
    # about which part came from which piece of equipment.
    if _new_old_direction_reversed(body, cleaned):
        return None
    # Rule 22/23 guard: never let a rewrite claim resolution the technician
    # explicitly said didn't happen -- see _claims_unwarranted_resolution().
    if _claims_unwarranted_resolution(text, cleaned):
        return None
    # Wrong-domain vocabulary guard -- see _uses_wrong_domain_vocabulary().
    # Proven live: TSP0008's "info link" (this shop's own term for a
    # physical link/tag on the unit) got reinterpreted as "information
    # hyperlink", treating a mechanical part name as if it were a web term.
    if _uses_wrong_domain_vocabulary(body, cleaned):
        return None
    # Flowery/vague AI-padding vocabulary guard -- see
    # _uses_flowery_padding_vocabulary(). Proven live: clear technician text
    # got paraphrased into corporate filler ("hiccup", "accommodate") and
    # the technical meaning drifted ("recalibrated" -> "reconfiguring
    # settings") in the same rewrite.
    if _uses_flowery_padding_vocabulary(body, cleaned):
        return None
    return cleaned


def rewrite(text: str) -> str:
    """Rewrite a single remark. Input via build_input().
    Returns description only (no asset ID prefix). Falls back to original on failure.

    Model escalation: tries the fastest available model first; if its output
    gets rejected by a safety guard (garbage, dropped detail, fabrication --
    see _rewrite_with_model()), retries with the next-better model in
    _AVAILABLE_MODELS before giving up to the zero-AI deterministic fallback.
    Proven live 2026-09-16: phi4-mini fabricated an invented "circuit path"
    / "suspect connector" narrative on a real technician remark, correctly
    got rejected by _dropped_part_nouns, and -- before escalation existed --
    fell all the way back to bare cleanup, which looked to the MST like the
    AI "wasn't doing anything" on exactly the messy remarks that need it
    most. Escalation keeps phi4-mini's speed for the common case while still
    reaching for a more careful model instead of giving up immediately.
    """
    if not text or not text.strip():
        return text

    # asset_id is only present when the input starts with "ASSETID / " AND
    # that prefix actually looks like an asset ID -- not just any text
    # containing " / " (see _looks_like_asset_id).
    asset_id = ""
    if " / " in text:
        candidate = text.split(" / ", 1)[0].strip()
        if _looks_like_asset_id(candidate):
            asset_id = candidate

    body = text.split(" / ", 1)[1] if asset_id else text

    # 2026-09-19: AI rewriting DISABLED by default per Isaac's explicit,
    # final call -- he wants deterministic spelling/punctuation/grammar
    # correction only, with a hard guarantee that identical input always
    # produces identical output. A generative model cannot make that
    # guarantee (proven live: the same TSP0008 raw remarks produced three
    # different AI outputs across three separate report runs tonight).
    # _deterministic_fallback() (despite the name -- it's the primary path
    # now, not just a failure fallback) handles asset-ID stripping,
    # first-person removal, tense normalization, and dictionary-based
    # spelling correction, all rule-based and reproducible. Every AI/model-
    # escalation code path below is left fully intact and working -- only
    # bypassed -- so it can be flipped back on later (_USE_AI = True) once
    # there's time to make a generative model behave the way he actually
    # wants without a full night of guard-patching per new failure mode.
    if not _USE_AI:
        return _deterministic_fallback(text, asset_id)

    if " | " not in body and len(body.split()) <= _MIN_WORDS_FOR_AI:
        return _simple_cleanup(body)

    # Give longer inputs more room -- rough heuristic: 1 token ≈ 4 chars.
    # Cap at 400 so we never blow up on garbage input.
    input_words = len(text.split())
    num_predict  = max(180, min(400, input_words * 2))

    context_block = _asset_type_context(asset_id)
    models_to_try = _AVAILABLE_MODELS or [_MODEL]
    for attempt, model in enumerate(models_to_try):
        is_last_attempt = attempt == len(models_to_try) - 1
        # Escalation timeout tiering: earlier (faster, riskier) models get a
        # short leash so a hang doesn't eat the whole budget before we even
        # get to try a better model -- only the LAST attempt (nowhere left
        # to escalate to) gets the full patience of _TIMEOUT_SINGLE.
        # Without this, 3 escalation attempts could each wait up to 280s,
        # a worst case of ~14 minutes for one remark. Proven live: a
        # 3-model escalation on a real remark took 7+ minutes end to end
        # before this tiering was added.
        call_timeout = _TIMEOUT_SINGLE if is_last_attempt else _ESCALATION_TIMEOUT
        cleaned = _rewrite_with_model(
            model, text, asset_id, body, context_block, num_predict,
            timeout=call_timeout,
        )
        if cleaned is not None:
            if attempt > 0:
                log.info(
                    "[ai_rewriter] Escalated to %s after %d rejection(s)",
                    model, attempt,
                )
            return cleaned
    return _deterministic_fallback(text, asset_id)


def rewrite_batch(inputs: list[str]) -> list[str]:
    """Rewrite all inputs, one at a time for best quality.

    phi4-mini handles one focused entry better than a long batch prompt.
    Ollama queues requests anyway so total time is similar to batching.
    """
    return [rewrite(t) for t in inputs]

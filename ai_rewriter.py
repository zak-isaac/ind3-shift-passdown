"""Local AI remark rewriter -- Ollama + Phi-4 Mini.

Public API
----------
check_ollama() -> bool
rewrite(text: str) -> str
rewrite_batch(inputs: list[str]) -> list[str]
    inputs format: "ASSET_ID|||remark1|||remark2|||..."
    The ||| separator tells the AI these are distinct tech remarks to synthesize.
    Output does NOT include the asset ID (report already shows it).
"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

import httpx

# -- Configuration -------------------------------------------------------------
_OLLAMA_BASE    = "http://localhost:11434"
_TIMEOUT_SINGLE = 90.0
_TIMEOUT_BATCH  = 90.0
_TEMPERATURE    = 0.1

# Model priority list -- first available one wins
_MODEL_PRIORITY = ["phi4:14b", "llama3.1:8b", "phi4-mini"]
_MODEL          = "phi4-mini"  # updated at startup by _detect_best_model()

log = logging.getLogger(__name__)


def _detect_best_model() -> str:
    """Pick the best available Ollama model from priority list."""
    global _MODEL
    try:
        import httpx as _httpx
        r = _httpx.get(f"{_OLLAMA_BASE}/api/tags", timeout=5)
        available = {m["name"].split(":")[0] + ":" + m["name"].split(":")[1]
                     if ":" in m["name"] else m["name"]
                     for m in r.json().get("models", [])}
        for candidate in _MODEL_PRIORITY:
            if candidate in available:
                _MODEL = candidate
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
5. Rewrite remarks using concise industrial maintenance language that reads like it was
   written by an experienced maintenance technician.

Technician Language
6. Remove all first-person language.
   I replaced -> Replaced | We adjusted -> Adjusted | I noticed -> Found | I checked -> Inspected
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
10. Preserve all directions exactly: Left, Right, Upper, Lower, Front, Rear, Top, Bottom.
11. Preserve all measurements, work order numbers, fault codes, locations, identifiers,
    quantities, and equipment references exactly.
12. Preserve technician maintenance vocabulary whenever technically correct.
    Keep: Unjammed, Swapped, Power cycled, Recalibrated, Realigned, Deinstalled, Lubricated,
    Tightened, Pulled, Out of tape, Boxes getting stuck, Box crushing, Paper done, Sensor flickering.
    Never rewrite into: Jamming issue resolved / Replaced with a spare unit /
    Restart sequence initiated / Investigation revealed / Additional issues affecting asset performance.
13. Preserve maintenance acronyms exactly. Never expand:
    LOTO, VFD, PLC, HMI, PM, RTS, MST.

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
    next troubleshooting step occurred.
    Example: Following calibration, the asset continued crushing boxes during operation.
    Technical Support was contacted. Technical Support recommended troubleshooting the X6
    photo eye. X6 photo eye inspected.

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
- The final output reads like an experienced industrial maintenance technician's
  passdown -- not an AI-generated report.

Output only the rewritten note. Nothing else.
"""

_SINGLE_TEMPLATE = (
    "Rewrite the following technician remark as a professional engineering shift passdown entry."
    " Follow all rules exactly. Output only the rewritten note. Do not include the asset ID.\n"
    "---\n"
    "Original: {text}\n"
    "Passdown:"
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
    if not any(_MODEL in m for m in models):
        print(f"\n{_sep}\n[AI REWRITER] phi4-mini NOT INSTALLED -- run: ollama pull phi4-mini\n{_sep}\n", flush=True)
        log.warning("[ai_rewriter] phi4-mini not found. Installed: %s", models)
        return False

    print(f"[ai_rewriter] Warming up '{_MODEL}'...", flush=True)
    try:
        _call_ollama("warmup", num_predict=10)
    except Exception:
        pass

    print(f"[ai_rewriter] Ready.", flush=True)
    return True


# -- Core ----------------------------------------------------------------------

def _call_ollama(prompt: str, timeout: float = _TIMEOUT_SINGLE,
                 num_predict: int = 150, stop: list[str] | None = None) -> Optional[str]:
    if stop is None:
        stop = ["\n\n", "Note:", "Examples", "<Your", "<your"]
    t0 = time.perf_counter()
    try:
        resp = _get_client().post(
            "/api/generate",
            json={
                "model":  _MODEL,
                "system": _SYSTEM_PROMPT,
                "prompt": prompt,
                "stream": False,
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
    """Safety net: remove asset ID from start of output if AI included it anyway."""
    if not asset_id:
        return text
    # Match "ASSETID" or "ASSETID:" or "ASSETID -" at the very start
    pattern = rf"^{re.escape(asset_id)}\s*[-:/]?\s*"
    return re.sub(pattern, "", text, flags=re.IGNORECASE).strip()


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
    return len(text) < 10 or any(g in low for g in (
        "<", "your answer", "your response", "[insert", "[your", "n/a"
    ))


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
    # 'Assisted/Assisting with X' -> direct action verb
    (r"(?i)\bAssist(?:ed|ing) with adjusting\b",   "Adjusted"),
    (r"(?i)\bAssist(?:ed|ing) with replacing\b",   "Replaced"),
    (r"(?i)\bAssist(?:ed|ing) with installing\b",  "Installed"),
    (r"(?i)\bAssist(?:ed|ing) with calibrating\b", "Calibrated"),
    (r"(?i)\bAssist(?:ed|ing) with deinstalling\b","Deinstalled"),
    (r"(?i)\bAssist(?:ed|ing) with reinstalling\b","Reinstalled"),
    (r"(?i)\bAssist(?:ed|ing) with\b",             "Performed"),
    (r"(?i)\bAssisting\b",                          "Performing"),
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


# -- Public API ----------------------------------------------------------------

def build_input(asset_id: str, remarks: list[str]) -> str:
    """Build the canonical input string for rewrite / rewrite_batch.

    Applies Python-level typo fixes before sending to the AI -- more
    reliable than depending on the model to catch them every time.
    Format: "ASSET_ID / remark1 | remark2 | ..."
    """
    fixed = [_fix_typos(r.strip()) for r in remarks if r.strip()]
    body  = " | ".join(fixed)
    if not asset_id:
        return body  # sub-bullet inputs: no prefix, just the remark
    return f"{asset_id} / {body}"


def rewrite(text: str) -> str:
    """Rewrite a single remark. Input via build_input().
    Returns description only (no asset ID prefix). Falls back to original on failure."""
    if not text or not text.strip():
        return text

    # asset_id is only present when the input starts with "ASSETID / "
    asset_id = ""
    if " / " in text:
        asset_id = text.split(" / ", 1)[0].strip()

    # Give longer inputs more room -- rough heuristic: 1 token ≈ 4 chars.
    # Cap at 400 so we never blow up on garbage input.
    input_words = len(text.split())
    num_predict  = max(180, min(400, input_words * 2))

    result = _call_ollama(
        _SINGLE_TEMPLATE.format(text=text, context_block=""),
        timeout=_TIMEOUT_SINGLE,
        num_predict=num_predict,
        stop=["---", "\nOriginal:", "\nNote:", "\nExample", "\n\n"],
    )
    if not result or _is_garbage(result):
        return text
    cleaned = _clean_output(_strip_leading_asset_id(result, asset_id))
    # AI signals "nothing worth reporting" with a bare dash
    if cleaned.strip() in ("-", "--", ""):
        return ""
    return cleaned


def rewrite_batch(inputs: list[str]) -> list[str]:
    """Rewrite all inputs, one at a time for best quality.

    phi4-mini handles one focused entry better than a long batch prompt.
    Ollama queues requests anyway so total time is similar to batching.
    """
    return [rewrite(t) for t in inputs]

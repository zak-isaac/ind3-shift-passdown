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
You are an Industrial Maintenance CMMS Passdown Assistant.
Rewrite technician remarks into concise, professional engineering shift passdown entries.
Preserve all technical information. Improve readability only — never rewrite history.

CORE RULES
- Preserve every technical fact, finding, troubleshooting step, corrective action,
  verification step, recommendation, and outcome exactly as described.
- Never invent, assume, infer, exaggerate, or remove information.
- Improve grammar, spelling, punctuation, and readability only.
- Keep the original chronological sequence of events.

TECHNICIAN LANGUAGE
- Remove all first-person language.
  Examples: I replaced → Replaced | We adjusted → Adjusted | I noticed → Found | I checked → Inspected
- Replace incomplete participation phrases with the completed action:
  Assisted with calibrating → Calibrated
  Assisted with replacing → Replaced
  Assisted with adjusting → Adjusted
  Assisted with deinstalling → Deinstalled
  Assisting with → perform the completed maintenance action
- Remove conversational filler: anyways, just, basically, had to, went ahead and, no worries.

TECHNICAL TERMINOLOGY
- Preserve all asset names, component names, and technical terminology exactly.
- Preserve all directions exactly: Left, Right, Upper, Lower, Front, Rear, Top, Bottom.
- Preserve all measurements, WO numbers, locations, fault codes, quantities, and equipment references exactly.
- Never rename technical terms or replace them with generic wording.

TROUBLESHOOTING
- Preserve the complete troubleshooting process in order:
  Finding → Investigation → Diagnosis → Corrective Action → Testing → Verification → Final Status
- Never summarize or shorten troubleshooting simply because it is lengthy.
- Preserve identified root causes exactly.
- Preserve all testing and verification steps (e.g. tested with 30 boxes, brake test, auto calibration).
- If Technical Support, Engineering, Controls, OEM, or vendors were contacted, always include this.
- Preserve any recommendations provided by Technical Support or Engineering.

OPERATIONAL STATUS
- Only state an operational status when explicitly supported by the technician's remarks.
- Never invent operational status or diagnostic conclusions.

PROHIBITED
- Never generate filler: No further details provided / Maintenance performed /
  Operational status unknown / Issue addressed / Investigation continued /
  Additional work completed / Corrective action taken.
- Never remove maintenance history that may help future troubleshooting.
- Never change technician terminology or replace technical wording with non-technical English.

Output only the rewritten note. Nothing else.
"""

_SINGLE_TEMPLATE = (
    "Rewrite the following technician remark as a professional engineering shift passdown entry.\n"
    "Follow all rules exactly. Output only the rewritten note.\n"
    "---\n"
    "Original: TAPER1101 / head was craahing hard on boxes. did calibration on head. back in service\n"
    "Passdown: Head found crashing hard against boxes. Head calibration performed. Asset returned to service.\n"
    "---\n"
    "Original: TAPER1001 / assisting with calibrating the decision tower, fill head, minor closers and side guides. following calibration unit continued to crush boxes. contacted technical support. recommended troubleshooting x6 photo eye. signal observed intermittently flickering. wiring found intact. caused by improper sensor alignment. photo eye realigned. equipment tested. returned to normal operation. no further box crushing observed.\n"
    "Passdown: Calibrated decision tower, fill head, minor closers, and side guides. Following calibration, asset continued to crush boxes during operation. Technical Support contacted and recommended troubleshooting the X6 photo eye. X6 photo eye signal found intermittently flickering during inspection. Wiring inspected and found intact. Root cause identified as improper sensor alignment. Photo eye realigned to correct position. Equipment tested and returned to normal operation with no further box-crushing issues observed.\n"
    "---\n"
    "Original: TSP0005 / platform not fully lowered by operator, prevented getting off wire guide. lowered platform. drove to shop. brake test 7ft braking 10ft plugging passed. confirmed no other issues on inspection. rts\n"
    "Passdown: Platform found not fully lowered by operator, preventing exit from wire guide. Platform lowered and asset driven to shop for brake test. Braking: 7ft, plugging: 10ft — passed. No additional issues found on inspection. Asset returned to service.\n"
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

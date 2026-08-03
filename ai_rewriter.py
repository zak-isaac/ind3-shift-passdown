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


log = logging.getLogger(__name__)



_SYSTEM_PROMPT = (
    "You rewrite warehouse maintenance notes into professional shift passdown entries. "
    "Rules: use passive voice, keep every detail the tech wrote, keep all part names exactly "
    "(head, closer, side guide, blushing, taper, closers -- do not change these), keep all "
    "directions (left/right/top/bottom), fix obvious typos, do not add anything new. "
    "Output only the rewritten note, nothing else."
)

_SINGLE_TEMPLATE = (
    "Rewrite as a professional passdown note. Keep all details. Keep all part names."
    " Do not add anything not in the original.\n"
    "---\n"
    "Original: TAPER1101 / head was craahing hard on boxes. did calibration on head. back in service\n"
    "Passdown: Head found crashing hard against boxes; head calibration performed and unit returned to service.\n"
    "---\n"
    "Original: VOIDFILL / blushing on right closure installed on wrong side. repositioned blushing. recalibrated head, side guide, closers. asset operational\n"
    "Passdown: Right-closure blushing found mis-installed on wrong side; blushing repositioned and head, side guide, and closers recalibrated; unit returned to service.\n"
    "---\n"
    "Original: VOIDFILL0201 / top and bottom tape threaded incorrectly by associate. rethreaded top and bottom correctly. asset fully operational\n"
    "Passdown: Top and bottom tape found threaded incorrectly by associate; both rethreaded correctly and unit returned to full operational status.\n"
    "---\n"
    "Original: TSP0005 / platform not fully lowered by operator, prevented getting off wire guide. lowered platform. drove to shop. brake test 7ft braking 10ft plugging passed | confirmed no other issues on inspection. rts\n"
    "Passdown: Platform found not fully lowered by operator, preventing exit from wire guide; platform lowered and unit driven to shop for brake test. Braking: 7ft, plugging: 10ft -- passed. No additional issues found on inspection; unit returned to service.\n"
    "---\n"
    "Original: {text}\n"
    "Passdown:"
)

_BATCH_TEMPLATE = (
    "Rewrite each numbered maintenance note as a complete shift passdown entry.\n"
    "The next shift reads these -- they need EVERY detail without pulling work orders.\n"
    "Asset ID is shown in the report header -- do NOT include it in your output.\n"
    "Keep all part names exactly as written (head, closer, side guide, blushing, taper, etc.).\n"
    "Keep all directional refs (left/right/top/bottom/front/rear) and all qualifiers (hard, repeatedly, etc.).\n"
    "For ' | ' separated remarks: present all actions in order -- do not collapse or drop any.\n"
    "Output a numbered list -- one entry per line. No blank lines between entries.\n\n"
    "Examples:\n"
    "  1. TAPER1101 / head was craahing hard on boxes. calibration done. back in service\n"
    "  1. Head found crashing hard against boxes; head calibration performed and unit returned to service.\n"
    "  2. VOIDFILL / blushing on right closure wrong side. repositioned. recalibrated head, side guide, closers. operational\n"
    "  2. Right-closure blushing found mis-installed on wrong side; blushing repositioned and head, side guide, and closers recalibrated; unit returned to service.\n"
    "  3. TSP0005 / platform not lowered, couldnt get off wire. lowered platform. brake test 7ft braking 10ft plugging passed | no other issues found\n"
    "  3. Platform found not fully lowered, preventing exit from wire guide; platform lowered and brake test performed -- braking: 7ft, plugging: 10ft, passed. No additional issues found; unit returned to service.\n"
    "  4. RC0042 / horn dead replaced fuse | wiring checked all good unit rts\n"
    "  4. Horn found inoperative; fuse replaced. Wiring inspected, all connections found good; unit returned to service.\n\n"
    "Notes to rewrite:\n"
    "{numbered_notes}\n\n"
    "Rewritten entries (STOP after the last numbered entry -- no extra text):\n"
    "1."
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
    (r"back in business\.?",           "unit returned to service."),
    (r"good to go!?",                  "unit returned to service."),
    (r"ready to roll!?",               "unit returned to service."),
    (r"up and running",                "returned to service"),
    (r"making it functional again\.?", "unit returned to service."),
    (r"(?i)\ball systems(?:\s+are)?\s+go\b", ""),
    (r"as usual\.?",                  ""),
    (r"no worries\.?",                ""),
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
    fixed   = [_fix_typos(r.strip()) for r in remarks if r.strip()]
    body    = " | ".join(fixed)
    return f"{asset_id} / {body}"


def rewrite(text: str) -> str:
    """Rewrite a single remark. Input via build_input().
    Returns description only (no asset ID prefix). Falls back to original on failure."""
    if not text or not text.strip():
        return text

    asset_id = text.split("/")[0].strip() if "/" in text else ""

    result = _call_ollama(
        _SINGLE_TEMPLATE.format(text=text, context_block=""),
        timeout=_TIMEOUT_SINGLE,
        num_predict=180,
        stop=["---", "\nOriginal:", "\nNote:", "\nExample", "\n\n"],
    )
    if not result or _is_garbage(result):
        return text
    return _clean_output(_strip_leading_asset_id(result, asset_id))


def rewrite_batch(inputs: list[str]) -> list[str]:
    """Rewrite all inputs, one at a time for best quality.

    phi4-mini handles one focused entry better than a long batch prompt.
    Ollama queues requests anyway so total time is similar to batching.
    """
    return [rewrite(t) for t in inputs]

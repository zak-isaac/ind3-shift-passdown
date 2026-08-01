"""Local AI remark rewriter -- Ollama + Phi-4 Mini.

Public API
----------
check_ollama() -> bool
    Call once at startup. Prints clear admin messages if Ollama or the
    model is missing. Returns True when fully ready.

rewrite(text: str) -> str
    Rewrite technician remarks into professional maintenance language.
    Input should be: "ASSET_ID / raw remark text"
    Returns the AI-rewritten text on success.
    Returns the ORIGINAL text unchanged on any failure.
    Report generation is NEVER blocked by this function.

Design constraints
------------------
- Communicates ONLY with http://localhost:11434 (never the internet).
- Reuses a single httpx.Client across all calls (connection pooling).
- Hard 60-second timeout per request.
- Does NOT log remark content unless DEBUG level is active.
- Does NOT modify report formatting, sections, or parser output.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

# -- Configuration -------------------------------------------------------------
_OLLAMA_BASE  = "http://localhost:11434"
_MODEL        = "phi4-mini"
_TIMEOUT_SEC  = 60.0
_TEMPERATURE  = 0.1
_MAX_TOKENS   = 200

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a maintenance technician writing concise Work Order notes for a "
    "warehouse fulfillment center (IND3). Equipment includes tappers, closers, "
    "void fill machines, chargers, dock doors, conveyors, forklifts, and reach trucks.\n"
    "Rewrite the raw technician remark as 1-2 professional WO-style sentences.\n"
    "Rules you MUST follow:\n"
    "1. Write in professional third-person passive voice: 'Calibration performed', 'Component repositioned'.\n"
    "2. Preserve asset IDs, WO numbers, part names, and measurements EXACTLY as written.\n"
    "3. Keep ALL facts: every symptom, every action taken, and the final status.\n"
    "4. Correct obvious typos such as doubled or transposed letters when the correct word "
    "is unambiguous from context. Example: 'craahing' -> 'crashing', 'recalibreted' -> 'recalibrated'.\n"
    "5. DO NOT add ANY detail not in the original text. Do not interpret or diagnose.\n"
    "6. Use the technician's exact words for the symptom and action, just make them grammatical.\n"
    "7. Output ONLY the rewritten sentence. No explanations, no labels, no WO number prefix."
)

_PROMPT_TEMPLATE = (
    "Examples of raw note to WO-style passdown entry:\n"
    "  Note: TAPER1001 / recalibrated the closer. asset is fully operational\n"
    "  Entry: TAPER1001 closer recalibration performed; unit returned to service.\n"
    "  Note: VOIDFILL0201 / top and bottom tape was threaded incorrectly, rethreaded correctly, asset fully operational\n"
    "  Entry: VOIDFILL0201 tape threading error corrected; unit returned to full operational status.\n"
    "  Note: TAPER1101 / the head was craahing hard on the boxes. did calibration on the head. asset back into operation\n"
    "  Entry: TAPER1101 head was crashing against boxes; head recalibrated and unit returned to service.\n"
    "  Note: VOIDFILL / blushing on right closure installed on wrong side. repositioned blushing. recalibrated head, side guide, closers. asset operational\n"
    "  Entry: VOIDFILL right-closure blushing found mis-installed; repositioned and head, side guide, and closers recalibrated; unit returned to service.\n"
    "  Note: RC0042 / horn not working replaced fuse rts\n"
    "  Entry: RC0042 horn found inoperative; fuse replaced and unit returned to service.\n\n"
    "Now write the entry for this note. Output ONLY the entry sentence, nothing else:\n"
    "  Note: {text}\n"
    "  Entry:"
)

# -- Singleton HTTP client (initialized once, reused for every call) -----------
_client: Optional[httpx.Client] = None


def _get_client() -> httpx.Client:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.Client(
            base_url=_OLLAMA_BASE,
            timeout=httpx.Timeout(_TIMEOUT_SEC, connect=5.0),
        )
    return _client


# -- Startup check -------------------------------------------------------------

def check_ollama() -> bool:
    """Verify Ollama is running and phi4-mini is installed.

    Prints administrator-facing messages when something is wrong.
    Returns True only when the full AI pipeline is ready.
    Call once during application startup -- not on every request.
    """
    _sep = "=" * 68

    # 1. Is Ollama running?
    try:
        resp = _get_client().get("/api/tags", timeout=5.0)
        resp.raise_for_status()
    except Exception as exc:
        print(
            f"\n{_sep}\n"
            "[AI REWRITER] OLLAMA IS NOT RUNNING\n"
            "  The local AI remark rewriter requires Ollama to be installed\n"
            "  and running on this machine.\n\n"
            "  Install steps:\n"
            "    1. Download Ollama from https://ollama.com/download\n"
            "    2. Run the installer\n"
            "    3. Open a terminal and run:  ollama pull phi4-mini\n"
            "    4. Restart this server\n\n"
            "  Until then, reports will use the original parsed remarks.\n"
            f"{_sep}\n",
            flush=True,
        )
        log.warning("[ai_rewriter] Ollama not reachable at startup: %s", exc)
        return False

    # 2. Is phi4-mini installed?
    models = [m.get("name", "") for m in resp.json().get("models", [])]
    if not any(_MODEL in m for m in models):
        print(
            f"\n{_sep}\n"
            "[AI REWRITER] PHI-4 MINI MODEL NOT INSTALLED\n"
            "  Ollama is running but the required model is missing.\n\n"
            "  To install it, open a terminal and run:\n"
            "    ollama pull phi4-mini\n\n"
            "  Until then, reports will use the original parsed remarks.\n"
            f"{_sep}\n",
            flush=True,
        )
        log.warning("[ai_rewriter] phi4-mini not found. Installed models: %s", models)
        return False

    # 3. Warm up the model -- first inference loads weights into memory (~30s).
    #    Doing it at startup means the first real report isn't slow.
    print(f"[ai_rewriter] Warming up '{_MODEL}' (first load may take ~30s)...", flush=True)
    try:
        _call_ollama("warmup")
    except Exception:
        pass  # warmup failure is non-fatal

    print(f"[ai_rewriter] Ready -- Ollama running, model '{_MODEL}' available.", flush=True)
    return True


# -- Core rewrite --------------------------------------------------------------

def _call_ollama(text: str) -> Optional[str]:
    """Send text to Ollama generate API. Returns rewritten string or None on any failure."""
    log.debug("[ai_rewriter] Starting rewrite (%d chars)", len(text))
    t0 = time.perf_counter()

    try:
        resp = _get_client().post(
            "/api/generate",
            json={
                "model":  _MODEL,
                "system": _SYSTEM_PROMPT,
                "prompt": _PROMPT_TEMPLATE.format(text=text),
                "stream": False,
                "options": {
                    "temperature": _TEMPERATURE,
                    "num_predict": _MAX_TOKENS,
                    "stop": ["\n\n", "Note:", "Example:", "<Your", "<your", "Note ", "Examples"],
                },
            },
        )
        resp.raise_for_status()
        result = resp.json().get("response", "").strip()
        elapsed = time.perf_counter() - t0

        if not result:
            log.warning("[ai_rewriter] Empty response -- falling back to original")
            return None

        log.info("[ai_rewriter] Completed in %.2fs", elapsed)
        return result

    except httpx.TimeoutException:
        log.warning("[ai_rewriter] Timed out after %.0fs -- using original remarks", _TIMEOUT_SEC)
        return None
    except httpx.ConnectError:
        log.warning("[ai_rewriter] Ollama not reachable -- using original remarks")
        return None
    except Exception as exc:
        log.warning("[ai_rewriter] Unexpected error -- using original remarks: %s", exc)
        return None


def rewrite(text: str) -> str:
    """Rewrite technician remarks using the local Phi-4 Mini model.

    Input format: "ASSET_ID / raw remark text"
    Always returns a string. On any failure (timeout, Ollama down,
    model missing, empty response) returns the original text unchanged.
    Report generation is never blocked by this function.
    """
    if not text or not text.strip():
        return text

    result = _call_ollama(text)

    if result is None:
        log.info("[ai_rewriter] Fallback active -- returning original remarks")
        return text

    # Reject template filler the model sometimes outputs
    _garbage = ("<", "your answer", "your response", "[insert", "[your", "n/a")
    if len(result) < 10 or any(g in result.lower() for g in _garbage):
        log.info("[ai_rewriter] Rejected template output -- using original")
        return text

    return result

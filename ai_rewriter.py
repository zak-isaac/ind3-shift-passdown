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
_OLLAMA_BASE      = "http://localhost:11434"
_MODEL            = "phi4-mini"
_TIMEOUT_SINGLE   = 60.0
_TIMEOUT_BATCH    = 120.0
_TEMPERATURE      = 0.1
_TOKENS_PER_ENTRY = 200  # 200k was requested but let's not melt the GPU lmao

log = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "You are a maintenance technician writing concise Work Order notes for a "
    "warehouse fulfillment center (IND3). Equipment includes tappers, closers, "
    "void fill machines, chargers, dock doors, conveyors, forklifts, and reach trucks.\n"
    "You will receive a note in the format: ASSET_ID / remark (or multiple remarks separated by ' | ').\n"
    "Rewrite as 1-2 professional WO-style sentences.\n"
    "Rules:\n"
    "1. Write in professional third-person passive voice: 'Calibration performed', 'Component repositioned'.\n"
    "2. Preserve part names, measurements, and WO numbers EXACTLY as written.\n"
    "3. Keep ALL facts: every symptom, every action, and the final status.\n"
    "4. If multiple remarks are given (separated by ' | '), synthesize them into ONE coherent entry -- "
    "combine without repeating, resolve the final status from the latest remark.\n"
    "5. Correct obvious typos (doubled/transposed letters) only when unambiguous. "
    "Example: 'craahing' -> 'crashing'.\n"
    "6. DO NOT add any detail not in the original remarks.\n"
    "7. DO NOT start your output with the asset ID -- it is already shown in the report. "
    "Start directly with the action or finding.\n"
    "8. Output ONLY the rewritten sentence. No labels, no explanations."
)

_SINGLE_TEMPLATE = (
    "Examples (asset ID is context only -- do NOT repeat it in output):\n"
    "  Note: TAPER1001 / recalibrated the closer. asset is fully operational\n"
    "  Entry: Closer recalibration performed; unit returned to service.\n"
    "  Note: VOIDFILL0201 / top and bottom tape was threaded incorrectly, rethreaded correctly, asset fully operational\n"
    "  Entry: Tape threading error corrected; unit returned to full operational status.\n"
    "  Note: TAPER1101 / the head was craahing hard on the boxes. did calibration on the head. asset back into operation\n"
    "  Entry: Head was crashing against boxes; recalibrated and unit returned to service.\n"
    "  Note: VOIDFILL / blushing on right closure installed on wrong side. repositioned blushing. recalibrated head, side guide, closers. asset operational\n"
    "  Entry: Right-closure blushing found mis-installed; repositioned and head, side guide, and closers recalibrated; unit returned to service.\n"
    "  Note: RC0042 / horn not working replaced fuse rts | checked wiring all good\n"
    "  Entry: Horn found inoperative; fuse replaced, wiring verified, unit returned to service.\n\n"
    "Now write the entry. Output ONLY the sentence, no asset ID, nothing else:\n"
    "  Note: {text}\n"
    "  Entry:"
)

_BATCH_TEMPLATE = (
    "Rewrite each numbered maintenance note as a WO-style passdown entry.\n"
    "Asset ID is for context only -- do NOT include it in your output.\n"
    "If a note has multiple remarks separated by ' | ', synthesize them into one entry.\n"
    "Output a numbered list ONLY -- one line per entry. No blank lines. No extra text.\n\n"
    "Examples:\n"
    "  1. TAPER1101 / head was craahing on boxes. calibration done. back in service\n"
    "  1. Head was crashing against boxes; recalibrated and unit returned to service.\n"
    "  2. TSP0005 / platform not fully lowered by operator. lowered platform. drove to shop. brake test: 7ft braking, 10ft plugging\n"
    "  2. Platform found not fully lowered; lowered and unit driven to shop for brake test. Braking distance: 7ft, plugging distance: 10ft.\n"
    "  3. RC0042 / horn dead replaced fuse | wiring checked all good unit rts\n"
    "  3. Horn found inoperative; fuse replaced, wiring verified, unit returned to service.\n\n"
    "Notes to rewrite:\n"
    "{numbered_notes}\n\n"
    "Rewritten entries:\n"
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


def _is_garbage(text: str) -> bool:
    low = text.lower()
    return len(text) < 10 or any(g in low for g in (
        "<", "your answer", "your response", "[insert", "[your", "n/a"
    ))


# -- Public API ----------------------------------------------------------------

def build_input(asset_id: str, remarks: list[str]) -> str:
    """Build the canonical input string for rewrite / rewrite_batch.

    Format: "ASSET_ID / remark1 | remark2 | ..."
    Multiple remarks (multi-tech WOs) are joined with ' | ' so the AI
    knows to synthesize them rather than just clean up one remark.
    """
    body = " | ".join(r.strip() for r in remarks if r.strip())
    return f"{asset_id} / {body}"


def rewrite(text: str) -> str:
    """Rewrite a single remark. Input via build_input().
    Returns description only (no asset ID prefix). Falls back to original on failure."""
    if not text or not text.strip():
        return text

    # Extract asset ID for post-processing strip
    asset_id = text.split("/")[0].strip() if "/" in text else ""

    result = _call_ollama(
        _SINGLE_TEMPLATE.format(text=text),
        timeout=_TIMEOUT_SINGLE,
        num_predict=150,
    )
    if not result or _is_garbage(result):
        return text
    return _strip_leading_asset_id(result, asset_id)


def rewrite_batch(inputs: list[str]) -> list[str]:
    """Rewrite all inputs in ONE Ollama call -- far faster than N x rewrite().

    inputs: list of strings from build_input().
    Returns list of same length; failed items fall back to original input.
    Output strings have the asset ID stripped (report header already shows it).
    """
    if not inputs:
        return []
    if len(inputs) == 1:
        return [rewrite(inputs[0])]

    # Extract asset IDs for post-processing
    asset_ids = [t.split("/")[0].strip() if "/" in t else "" for t in inputs]

    numbered = "\n".join(f"  {i+1}. {t}" for i, t in enumerate(inputs))
    prompt   = _BATCH_TEMPLATE.format(numbered_notes=numbered)
    budget   = _TOKENS_PER_ENTRY * len(inputs)

    t0  = time.perf_counter()
    raw = _call_ollama(
        prompt,
        timeout=_TIMEOUT_BATCH,
        num_predict=budget,
        stop=[f"\n{len(inputs)+1}.", "Note:", "Examples",
              "Question:", "\nQ:", "\nA)", "What ", "---"],
    )
    log.info("[ai_rewriter] batch(%d) %.2fs", len(inputs), time.perf_counter() - t0)

    if not raw:
        log.warning("[ai_rewriter] Batch failed -- falling back to originals")
        return inputs[:]

    full = "1." + raw  # restore the "1." used as prompt suffix

    results: list[str] = []
    for i in range(len(inputs)):
        n      = i + 1
        n_next = i + 2
        m = re.search(rf"{n}\.\s*(.+?)(?=\n\s*{n_next}\.|$)", full, re.DOTALL)
        if m:
            parsed = m.group(1).strip()
            if not _is_garbage(parsed):
                results.append(_strip_leading_asset_id(parsed, asset_ids[i]))
            else:
                results.append(inputs[i])
        else:
            log.warning("[ai_rewriter] Could not parse item %d -- using original", n)
            results.append(inputs[i])

    return results

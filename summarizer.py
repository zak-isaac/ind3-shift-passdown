"""Remarks summarizer for CMMS-imported work orders.

When a single WO has remarks from multiple technicians, this module
produces a single coherent summary.

Priority order:
  1. Element LLM (Walmart GenAI) — if ELEMENT_API_KEY env var is set
  2. Smart fallback — deduplicates and joins remarks cleanly (ships today)

To activate Element: set the ELEMENT_API_KEY environment variable to
your provisioned token. No other code changes needed.
"""
import os


# ── Element LLM (plug in when key is ready) ──────────────────────────────────

_ELEMENT_BASE_URL = "https://element.walmart.com/api/v1"
_ELEMENT_MODEL    = "gpt-4o"   # confirm with Element docs / your provisioned model
_ELEMENT_API_KEY  = os.getenv("ELEMENT_API_KEY", "")

_SYSTEM_PROMPT = (
    "You are a reliability shift assistant at a Walmart distribution center. "
    "Technicians have logged multiple remarks for the same work order. "
    "Combine them into a single clear, concise summary (2-3 sentences max). "
    "Do not add information that wasn't in the original remarks. "
    "Write in past tense. Do not include technician names."
)


async def summarize_remarks(remarks: list[str]) -> str:
    """Return a single summary string for the given list of tech remarks.

    Falls back to smart concatenation when Element is not configured.
    """
    if not remarks:
        return ""

    if len(remarks) == 1:
        return remarks[0]

    if _ELEMENT_API_KEY:
        try:
            return await _summarize_with_element(remarks)
        except Exception as exc:
            print(f"[summarizer] Element call failed, using fallback: {exc}", flush=True)

    return _smart_concat(remarks)


async def _summarize_with_element(remarks: list[str]) -> str:
    """Call the Element LLM API (OpenAI-compatible) to produce a summary."""
    import httpx

    bullet_list = "\n".join(f"- {r}" for r in remarks)
    user_msg = f"Summarize these technician remarks for the same work order:\n{bullet_list}"

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{_ELEMENT_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {_ELEMENT_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": _ELEMENT_MODEL,
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                "max_tokens": 200,
                "temperature": 0.3,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def _smart_concat(remarks: list[str]) -> str:
    """Fallback: deduplicate and join remarks into a readable single string.

    Strips duplicate sentences across remarks so we don't repeat ourselves.
    """
    # Collect unique sentences across all remarks
    seen: set[str] = set()
    sentences: list[str] = []

    for remark in remarks:
        # Split on period/exclamation/question mark followed by space or end
        parts = [s.strip() for s in remark.replace("!", ".").replace("?", ".").split(".")]
        for part in parts:
            norm = part.lower().strip()
            if norm and norm not in seen:
                seen.add(norm)
                sentences.append(part.strip())

    if not sentences:
        return " | ".join(remarks)

    joined = ". ".join(s.rstrip(".") for s in sentences if s)
    return joined + "." if joined and not joined.endswith(".") else joined

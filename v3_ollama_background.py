"""Background Ollama drafts for grouped V3 work orders.

Opt-in with IND3_OLLAMA_REWRITE=1. Source remarks never overwritten.
No new imported / employer data leaves the computer through this service:
only an explicitly configured localhost Ollama endpoint is accepted.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

import db
from v3_report_layout import build_shift_happenings
from v3_proofread import proofread

MODEL = "qwen2.5:3b"
_LOCK = threading.Lock()
_WORKERS: dict[str, threading.Thread] = {}

# Ollama's local /api/chat supports JSON-schema constrained replies.
# This avoids the 3B model echoing instructions ("Consolidate...", "Remark 1").
# Every actual paragraph still passes the original technician-source verifier.
WORK_LOG_SCHEMA = {
    "type": "object",
    "properties": {"work_log": {"type": "string"}},
    "required": ["work_log"],
    "additionalProperties": False,
}

INSTRUCTIONS = """Edit industrial maintenance technician notes into one readable work log.
Use the technician notes as your ONLY factual source.
Correct spelling and grammar; combine exact repetitions.
Preserve EVERY distinct repair, troubleshooting action, observation, part,
measurement, fault code, and unresolved issue. If multiple people contributed,
preserve each person's distinct work without adding names.
Never invent repairs, results, tests, orders, parts, or returned-to-service claims.
A reported problem is not proof that anyone fixed it.
Do not add headings, technician-note labels, introductions, or instructions.
Put only the completed work-log text into the JSON field work_log."""


def enabled() -> bool:
    return os.environ.get("IND3_OLLAMA_REWRITE", "").strip() == "1"

def _db_path() -> Path:
    return Path(db.get_db_path()).resolve()

def _connect(path: Path):
    c = sqlite3.connect(str(path), timeout=15)
    c.row_factory = sqlite3.Row
    return c

def ensure_table(path: Path) -> None:
    with _connect(path) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS v3_ollama_drafts (
            shift_id INTEGER NOT NULL,
            wo_number TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            asset TEXT NOT NULL,
            cause TEXT NOT NULL,
            source_json TEXT NOT NULL,
            draft TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'queued',
            reason TEXT NOT NULL DEFAULT '',
            approved INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(shift_id, wo_number)
        )""")

def _source_hash(asset: str, cause: str, notes: list[str]) -> str:
    payload = json.dumps([asset, cause, notes], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()

def _group_sources(entries: list[dict], metadata: dict[int, dict]) -> list[dict]:
    """Use existing V3 grouping to define which WOs exist, raw details for AI."""
    current = {h.wo_number: h for h in build_shift_happenings(entries, metadata) if h.wo_number}
    grouped: dict[str, list[str]] = {}
    for entry in entries:
        if entry.get("excluded"):
            continue
        wo = str(entry.get("wo_number") or "").strip()
        if wo not in current:
            continue
        from v3_report_layout import _raw_remarks
        grouped.setdefault(wo, []).extend(_raw_remarks(entry))
    return [{"wo": wo, "asset": h.title, "cause": h.cause,
             "notes": grouped.get(wo, [])} for wo, h in current.items()]

def queue_shift(shift_id: int, retry_original: bool = False) -> int:
    if not enabled():
        return 0
    path = _db_path()
    ensure_table(path)
    with _connect(path) as c:
        entries = [dict(e) for e in c.execute(
            "SELECT * FROM entries WHERE shift_id=? AND excluded=0", (shift_id,))]
        try:
            metadata = {m["entry_id"]: dict(m) for m in c.execute(
                "SELECT * FROM v3_entry_meta WHERE shift_id=?", (shift_id,))}
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).casefold():
                raise
            metadata = {}
    count = 0
    grouped = _group_sources(entries, metadata)
    with _connect(path) as c:
        for x in grouped:
            if not x["notes"] or all(not t.strip() for t in x["notes"]):
                continue
            stamp = _source_hash(x["asset"], x["cause"], x["notes"])
            old = c.execute("SELECT source_hash,status,draft FROM v3_ollama_drafts WHERE shift_id=? AND wo_number=?",
                            (shift_id, x["wo"])).fetchone()
            if old and old["source_hash"] == stamp:
                rejected_stored_pass = (old["status"] == "passed" and
                    not _verify(x["notes"],old["draft"])[0])
                if not (retry_original and (
                        old["status"] == "original" or rejected_stored_pass)):
                    continue
            c.execute("""INSERT INTO v3_ollama_drafts
                (shift_id,wo_number,source_hash,asset,cause,source_json,draft,status,reason,approved)
                VALUES(?,?,?,?,?,?,'','queued','',0)
                ON CONFLICT(shift_id,wo_number) DO UPDATE SET
                source_hash=excluded.source_hash,asset=excluded.asset,cause=excluded.cause,
                source_json=excluded.source_json,draft='',status='queued',reason='',approved=0""",
                (shift_id,x["wo"],stamp,x["asset"],x["cause"],json.dumps(x["notes"])))
            count += 1
    start_worker(path)
    return count

def _technical_tokens(text: str) -> set[str]:
    return set(x.lower() for x in re.findall(
        r"\b\d+(?:\.\d+)?\s*(?:psi|ft|feet|mm|v|vac|a|amps|rpm|°f|°c)\b"
        r"|\b(?:[A-Z]{2,}[-.]?\d{2,}|[A-Z]\d{3,})\b", text, re.I))

# Action and component guardrails are intentionally restricted to claims that
# can materially change a maintenance report. Free word-by-word equality is
# not valid for human paraphrases ("changed" -> "replaced", "ran fine" ->
# "operated normally"). Hard data is independently checked below.
_ACTION_GROUPS = {
    "replace": r"\b(?:replac\w*|chang(?:e|ed|ing)|swapp\w*|install\w*)\b",
    "remove": r"\b(?:remov\w*|extract\w*|took\s+(?:out|off))\b",
    "adjust": r"\b(?:adjust\w*|recalibrat\w*|calibrat\w*|align\w*|realign\w*)\b",
    "clean": r"\b(?:clean\w*|wip\w*|clear\w*|wash\w*)\b",
    "reconnect": r"\b(?:reconnect\w*|reattach\w*|reseat\w*|plugged\s+(?:in|back))\b",
    "reset": r"\b(?:reset|reflash\w*|reboot\w*|power.?cycl\w*)\b",
    "tighten": r"\b(?:tighten\w*|retighten\w*|secur\w*)\b",
    "lubricate": r"\b(?:lubricat\w*|greas\w*|wd40)\b",
    "inspect": r"\b(?:inspect\w*|check\w*|diagnos\w*|troubleshoot\w*|test\w*|monitor\w*|watch\w*|observ\w*|ran\s+\d+\s+(?:cases|boxes))\b",
    "order": r"\b(?:order\w*|purchas\w*|request\w*)\b",
    "lockout": r"\b(?:lock(?:ed)?\s*out|loto)\b",
}
# Some action families describe equivalent shop work. This is deliberately
# not a general synonym generator; it is a guard against invented tasks.
_ACTION_ALIASES = {
    "replace": {"replace"}, "remove": {"remove"},
    "adjust": {"adjust"}, "clean": {"clean"},
    "reconnect": {"reconnect"}, "reset": {"reset"},
    "tighten": {"tighten","reconnect"},
    "lubricate": {"lubricate"}, "inspect": {"inspect"},
    "order": {"order"}, "lockout": {"lockout"}
}
_PART_GROUPS = {
    "fuse":r"\bfuses?\b", "valve":r"\bvalves?\b",
    "filter":r"\bfilters?\b", "hose":r"\bhoses?\b",
    "bearing":r"\bbearings?\b", "belt":r"\bbelts?\b",
    "motor":r"\bmotors?\b", "sensor":r"\bsensors?\b",
    "controller":r"\bcontrollers?\b", "roller":r"\brollers?\b",
    "blade":r"\bblades?\b", "screw":r"\bscrews?\b",
    "bolt":r"\bbolts?\b", "brake":r"\bbrakes?\b",
    "battery":r"\bbatter(?:y|ies)\b", "wire":r"\bwires?\b",
    "light":r"\blights?\b", "printer":r"\bprinters?\b",
    "paper":r"\bpaper\b", "tape":r"\btape\b",
    "head":r"\bheads?\b", "pad":r"\bpads?\b",
    "pulley":r"\bpulleys?\b", "cable":r"\bcables?\b",
    "reservoir":r"\breservoirs?\b", "coolant":r"\bcoolant\b",
    "thermostat":r"\bthermostats?\b",
    "box_or_case":r"\b(?:boxes|box|cases|case)\b",
    "air_line":r"\b(?:air\s+lines?|airlines?)\b",
}
_SUCCESS_CUE = re.compile(
    r"\b(?:rts|return(?:ed)?\s+to\s+service|resolved|fixed|repair(?:ed)?\s+completed|"
    r"(?:back|returned)\s+to\s+normal|working\s+(?:good|fine|properly|normally)|"
    r"(?:ran|running|operat(?:ing|ed)|function(?:ing|ed)?)\s+(?:good|fine|smoothly|normally|properly|well)|"
    r"(?:no|without)\s+(?:further\s+)?(?:issues|faults|problems)|"
    r"seams?\s+(?:ok|okay|good)|(?:ran|tested|watched)\s+\d+\s+(?:cases|boxes)\s+"
    r"(?:without|with\s+no)\s+(?:issues|faults|problems))\b", re.I)
_TERMS_STOPWORDS = {
    "the","a","an","in","on","at","and","or","to","of","for","by","from",
    "with","we","i","it","is","was","were","be","been","then","that",
    "this","as","after","before","had","have","has","there","are",
    "not","so","but","when","into","per","their","they","he","she",
    "got","get","some","more","did","all","were","what","them","while",
    "and","but","does","would","our","now","still",
}


def _factual_terms(value: str) -> set[str]:
    value = proofread(value).casefold()
    words = re.findall(r"[a-z0-9]+", value)
    def stem(w):
        if len(w)>6 and w.endswith("ing"):
            w=w[:-3]
        elif len(w)>5 and w.endswith("ed"):
            w=w[:-2]
        elif len(w)>5 and w.endswith("es"):
            w=w[:-2]
        elif len(w)>4 and w.endswith("s") and not w.endswith("ss"):
            w=w[:-1]
        return w
    return {stem(w) for w in words if len(w)>2 and w not in _TERMS_STOPWORDS}


def _ensure_short_contributions(notes: list[str], paragraph: str) -> str:
    """Preserve simple second-technician notes without inventing their work.

    Cetaris frequently has a second labor row containing only 'Assist'.
    AI tends to drop it despite being a distinct documented contribution.
    Preserve it in the accepted paragraph, without claiming an extra repair.
    """
    if any(note.strip().casefold().strip(".!") in
           ("assist","assisted","assisting","helped","assistance")
           for note in notes):
        if not re.search(r"\bassist\w*|\bhelp(?:ed|ing)?\b", paragraph, re.I):
            return paragraph.rstrip(" ;.") + "; Assisted."
    return paragraph


def _verify(source: list[str], result: str) -> tuple[bool, str]:
    if not result.strip() or len(result)>3000:
        return False,"Empty or oversized draft"
    src=" ".join(source)
    _instruction_leaks=(
        r"\bconsolidat(?:e|ed|ing|ion)\b",
        r"\bduplicat(?:e|ed|es|ing|ion)\b.{0,45}\b(?:action|remark|note|entr|line|word|statement|task|work)\b",
        r"\b(?:maintenance|technical|rewritten|consolidated)\s+(?:paragraph|summary|rewrite|note)\b",
        r"\b(?:source|original|technician)\s+remarks?\b",
        r"\b(?:remark|note)\s+\d+\s*:",
        r"\b(?:here (?:is|are)|below is)\s+(?:the|a|your|my)\s+(?:summary|rewrite|revised|consolidated|paragraph)\b",
        r"\b(?:return|write|generate)\s+(?:one|only|a)\s+(?:concise|professional|consolidated|paragraph)\b",
        r"\b(?:do not|don't|never|avoid)\s+(?:copy|invent|add|include|turn|repeat|assume)\b",
        r"\b(?:even if described in different wording|retain all distinct troubleshooting|reported issue .{0,15}context only)\b",
        r"\b(?:as an? ai|as (?:a|an) (?:technical|maintenance) editor|according to (?:the|your) instructions)\b",
    )
    if any(re.search(pattern,result,re.I) for pattern in _instruction_leaks):
        return False,"AI instruction or duplicate-analysis text leaked into technician remarks"

    # Measurements, work order identifiers and numeric counts are hard
    # evidence. Preserve every source value and reject any invented values.
    source_technical=_technical_tokens(src)
    draft_technical=_technical_tokens(result)
    if source_technical-draft_technical:
        return False,"Missing technical tokens: "+", ".join(sorted(source_technical-draft_technical))
    if draft_technical-source_technical:
        return False,"Invented technical tokens: "+", ".join(sorted(draft_technical-source_technical))
    source_numbers=set(re.findall(r"(?<![a-z0-9])\d+(?:\.\d+)?(?![a-z0-9])",src,re.I))
    draft_numbers=set(re.findall(r"(?<![a-z0-9])\d+(?:\.\d+)?(?![a-z0-9])",result,re.I))
    if source_numbers-draft_numbers:
        return False,"Missing source measurements or numeric values"
    if draft_numbers-source_numbers:
        return False,"Invented numeric values"

    if re.search(r"\bRTS\b|returned to service",result,re.I) and not re.search(
        r"\bRTS\b|returned to service",src,re.I):
        return False,"Unverified RTS"
    if re.search(r"\b(?:technician|tech)\s*[A-Z0-9]\b",result,re.I):
        return False,"Technician labels in draft"

    if re.search(r"\b(?:ordered|received|delivered)\b",result,re.I) and not re.search(
        r"\b(?:ordered|received|delivered)\b",src,re.I):
        return False,"Potential unsupported outcome"
    if re.search(r"\b(?:resolved|fully operational|fixed|no (?:further )?(?:issues|faults|problems)|"
                 r"running smoothly|operating smoothly|back to normal)\b",result,re.I):
        if not _SUCCESS_CUE.search(src):
            return False,"Unsupported all-clear claim"

    # Material maintenance actions/components require evidence. Do not reject
    # neutral grammar or an ordinary paraphrase merely for using new words.
    src_clean=proofread(src).casefold()
    draft_clean=proofread(result).casefold()
    src_actions={key for key,pat in _ACTION_GROUPS.items()
                 if re.search(pat,src_clean,re.I)}
    out_actions={key for key,pat in _ACTION_GROUPS.items()
                 if re.search(pat,draft_clean,re.I)}
    for action in out_actions:
        if not (_ACTION_ALIASES[action] & src_actions):
            return False,"AI introduced unsupported action: "+action
    src_parts={key for key,pat in _PART_GROUPS.items()
               if re.search(pat,src_clean,re.I)}
    out_parts={key for key,pat in _PART_GROUPS.items()
               if re.search(pat,draft_clean,re.I)}
    if out_parts-src_parts:
        return False,"AI introduced unsupported words: "+", ".join(sorted(out_parts-src_parts))

    # Check every distinct labor remark. A 75% *exact word overlap* rejected
    # sound paraphrases and even valid notes containing "Assist"; use a lower
    # fact overlap threshold while retaining hard actions/parts/numbers above.
    output_facts=_factual_terms(result)
    for note in source:
        facts=_factual_terms(note)
        if not facts:
            continue
        overlap=len(facts & output_facts)
        min_overlap=(1 if len(facts)<=3 else max(2,(len(facts)*2+4)//5))
        if overlap<min_overlap:
            return False,"Distinct technician source remark may be missing"
    return True,"Automatic source validation passed"


def _generate(item: sqlite3.Row) -> tuple[str, str, str]:
    notes = json.loads(item["source_json"])
    # Restrict to local Ollama, no remote API or outside network.
    data = json.dumps({
        "model": os.environ.get("IND3_OLLAMA_MODEL", MODEL),
        "stream": False, "keep_alive": "30m",
        "format": WORK_LOG_SCHEMA,
        "options": {"temperature": 0, "num_predict": 480},
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            # The previous prompt's labels and closing "Return only the
            # consolidated paragraph" instruction were echoed back into
            # work orders. Send only actual technician source notes as data.
            # Do not include the WO's reported issue as if it were work done.
            {"role": "user", "content": json.dumps(
                {"technician_notes": notes}, ensure_ascii=False)}
        ]
    }).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    # Large Cetaris imports can contain dozens of grouped WOs. Use a
    # bounded per-WO timeout, so an unresponsive local model cannot block the
    # entire queue indefinitely. Never call a remote model with Cetaris data.
    try:
        timeout = max(5, min(180, int(os.environ.get("IND3_OLLAMA_TIMEOUT_SEC", "45"))))
    except (TypeError, ValueError):
        timeout = 45
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            payload = json.load(response)
        raw_reply = str(payload.get("message", {}).get("content", "")).strip()
        # Modern Ollama returns a JSON object matching WORK_LOG_SCHEMA.
        # Plain text is accepted only as a backward-compatible fallback;
        # both forms must pass exactly the same source-accuracy validation.
        try:
            structured = json.loads(raw_reply)
        except (ValueError, TypeError):
            draft = raw_reply
        else:
            draft = str(structured.get("work_log", "")).strip() if isinstance(structured, dict) else ""
        draft = _ensure_short_contributions(notes, draft)
        good, reason = _verify(notes, draft)
        return (proofread(draft), "passed", "Automatic source validation passed") if good else ("", "original", reason)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            message = "Local Ollama model or endpoint not found (HTTP 404). Check ollama list."
        else:
            message = f"Local Ollama rejected the request (HTTP {exc.code})."
        return "", "original", message
    except (TimeoutError, OSError, urllib.error.URLError, ValueError) as exc:
        return "", "original", f"Local Ollama request failed or exceeded {timeout}s: {type(exc).__name__}"

def _worker(path: Path) -> None:
    try:
        while True:
            with _connect(path) as c:
                row = c.execute(
                    "SELECT * FROM v3_ollama_drafts WHERE status='queued' ORDER BY shift_id,wo_number LIMIT 1"
                ).fetchone()
                if row is None:
                    break
                c.execute("UPDATE v3_ollama_drafts SET status='writing' WHERE shift_id=? AND wo_number=? AND source_hash=?",
                          (row["shift_id"],row["wo_number"],row["source_hash"]))
            try:
                draft, status, reason = _generate(row)
            except Exception as exc:
                # Unexpected backend errors must never kill the queue worker
                # and strand every remaining work order as AI Processing.
                # Do not log the original technician text or error payload.
                draft, status = "", "original"
                reason = "Local AI processing error (" + type(exc).__name__ + "); original remarks retained"
            with _connect(path) as c:
                # CAS ensures re-import/approval is never overwritten by an old AI result.
                c.execute("""UPDATE v3_ollama_drafts SET draft=?,status=?,reason=?,approved=0
                    WHERE shift_id=? AND wo_number=? AND source_hash=? AND status='writing'""",
                    (draft,status,reason,row["shift_id"],row["wo_number"],row["source_hash"]))
    finally:
        with _LOCK:
            _WORKERS.pop(str(path), None)

def start_worker(path: Path) -> None:
    with _LOCK:
        k = str(path)
        if k in _WORKERS and _WORKERS[k].is_alive():
            return
        with _connect(path) as c:
            c.execute("UPDATE v3_ollama_drafts SET status='queued' WHERE status='writing'")
        thread = threading.Thread(target=_worker,args=(path,),daemon=True,name="v3-ollama-writer")
        _WORKERS[k] = thread
        thread.start()

def _eligible_drafts(shift_id: int) -> tuple[list[dict], dict[str, str]]:
    """Auto-accept verified drafts, never stale or unsupported AI text.

    Every read checks the source hash against live grouped technician remarks.
    A pre-patch 'passed' record is revalidated just like a fresh model draft.
    This is automatic: there is no MST approval workflow.
    """
    path = _db_path()
    ensure_table(path)
    with _connect(path) as c:
        drafts = [dict(r) for r in c.execute("""
            SELECT wo_number,asset,cause,status,reason,approved,draft,source_json,source_hash
            FROM v3_ollama_drafts WHERE shift_id=? ORDER BY wo_number
        """, (shift_id,))]
        entries = [dict(e) for e in c.execute(
            "SELECT * FROM entries WHERE shift_id=? AND excluded=0", (shift_id,))]
        try:
            metadata = {m["entry_id"]: dict(m) for m in c.execute(
                "SELECT * FROM v3_entry_meta WHERE shift_id=?", (shift_id,))}
        except sqlite3.OperationalError as exc:
            if "no such table" not in str(exc).casefold():
                raise
            metadata = {}
    current = {item["wo"]: item for item in _group_sources(entries, metadata)}
    edited = {str(entry.get("wo_number") or "") for entry in entries
              if metadata.get(entry["id"], {}).get("mst_edited")}
    validated = {}
    for record in drafts:
        wo = str(record["wo_number"])
        item = current.get(wo)
        if not item or wo in edited:
            record.update(status="original", draft="", reason="MST edit or excluded work order; original remarks used", approved=0)
            continue
        if record["source_hash"] != _source_hash(item["asset"], item["cause"], item["notes"]):
            record.update(status="original", draft="", reason="Remarks changed since AI rewrite; original remarks used", approved=0)
            continue
        if record["status"] != "passed":
            continue
        safe, reason = _verify(item["notes"], record["draft"])
        if not safe:
            record.update(status="original", draft="", reason=reason, approved=0)
            continue
        validated[wo] = record["draft"]
        record["approved"] = 1  # indicates automatic validation, not human approval
        record["reason"] = "AI validated against technician remarks; original source retained"
    return drafts, validated


def statuses(shift_id: int) -> list[dict]:
    """Return truthful progress and repair orphaned queue/writing states.

    The worker may disappear when Mac Terminal/server restarts while Ollama
    is still generating a WO. A status poll restarts only pending work;
    never marks a draft Passed until its source is verified.
    """
    drafts, _ = _eligible_drafts(shift_id)
    if enabled() and any(r["status"] in ("queued", "writing") for r in drafts):
        start_worker(_db_path())
        drafts, _ = _eligible_drafts(shift_id)
    pending = [r for r in drafts if r["status"] in ("queued", "writing")]
    queued = [r for r in drafts if r["status"] == "queued"]
    for r in drafts:
        r["queue_total"] = len(pending)
        if r["status"] == "queued":
            r["queue_position"] = next(
                (i + 1 for i, item in enumerate(queued)
                 if item["wo_number"] == r["wo_number"]), 0)
            r["reason"] = "Queued for local Ollama; " + str(len(pending)) + " remaining"
        elif r["status"] == "writing":
            r["queue_position"] = 0
            r["reason"] = "Local Ollama is rewriting this work order"
        else:
            r["queue_position"] = 0
    return drafts


def approved_for_report(shift_id: int) -> dict[str, str]:
    """Compatibility name: auto-validated paragraphs only; else original text."""
    _, validated = _eligible_drafts(shift_id)
    return validated

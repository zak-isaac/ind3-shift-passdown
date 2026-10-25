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

INSTRUCTIONS = """You are an industrial maintenance technical editor.
Return ONE concise professional paragraph using ONLY the technician SOURCE remarks.
Consolidate duplicated actions even if described in different wording.
Retain all distinct troubleshooting, repairs, measurements, codes, parts and statuses.
NEVER invent work, orders, tests, outcomes, parts, root causes or RTS.
NEVER turn intended, needed, planned or requested work into completed work.
If a fault remains, preserve it. Closed or Completed does not imply RTS.
Avoid technician names and references. No heading, bullet, label or explanation.
Do not copy the reported issue as a technician-performed action.
"""

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

def queue_shift(shift_id: int) -> int:
    if not enabled():
        return 0
    path = _db_path()
    ensure_table(path)
    with _connect(path) as c:
        entries = [dict(e) for e in c.execute(
            "SELECT * FROM entries WHERE shift_id=? AND excluded=0", (shift_id,))]
        metadata = {m["entry_id"]: dict(m) for m in c.execute(
            "SELECT * FROM v3_entry_meta WHERE shift_id=?", (shift_id,))}
    count = 0
    grouped = _group_sources(entries, metadata)
    with _connect(path) as c:
        for x in grouped:
            if not x["notes"] or all(not t.strip() for t in x["notes"]):
                continue
            stamp = _source_hash(x["asset"], x["cause"], x["notes"])
            old = c.execute("SELECT source_hash FROM v3_ollama_drafts WHERE shift_id=? AND wo_number=?",
                            (shift_id, x["wo"])).fetchone()
            if old and old["source_hash"] == stamp:
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

def _verify(source: list[str], result: str) -> tuple[bool, str]:
    if not result.strip() or len(result) > 3000:
        return False, "Empty or oversized draft"
    src = " ".join(source)
    if re.search(
        r"\bconsolidat(?:e|ed|ing)\b.{0,60}\b(?:maintenance|tasks?|actions?|paragraph|troubleshooting|duplicated)\b"
        r"|\bremark\s+\d+\s*:|\bsource remarks\b|\bhere is (?:the|a) (?:summary|rewrite)\b",
        result, re.I):
        return False, "AI instruction/summary label leaked into technician remarks"
    missing = _technical_tokens(src) - _technical_tokens(result)
    if missing:
        return False, "Missing technical tokens: " + ", ".join(sorted(missing))
    if re.search(r"\bRTS\b|returned to service", result, re.I) and not re.search(
        r"\bRTS\b|returned to service", src, re.I):
        return False, "Unverified RTS"
    if re.search(r"\b(?:technician|tech)\s*[A-Z0-9]\b", result, re.I):
        return False, "Technician labels in draft"
    # Conservative note-by-note coverage: reject dropped collaborator details.
    # Good paraphrases may be rejected; original remarks are always available.
    stopwords = {"the","a","an","in","on","at","and","or","to","of","for","by",
                 "from","with","we","i","it","is","was","were","be","been",
                 "then","that","this","as","after","before","had","have","has",
                 "there","are","not","so","but","when","into","per"}
    def facts(value):
        value = proofread(value).casefold()
        return {w for w in re.findall(r"[a-z0-9]+", value)
                if len(w) > 2 and w not in stopwords}
    out_facts = facts(result)
    for note in source:
        note_facts = facts(note)
        if note_facts and len(note_facts & out_facts) < max(1, (len(note_facts)*3+3)//4):
            return False, "Distinct technician source remark may be missing"
    source_numbers = set(re.findall(r"(?<![a-z0-9])\d+(?:\.\d+)?(?![a-z0-9])", src, re.I))
    output_numbers = set(re.findall(r"(?<![a-z0-9])\d+(?:\.\d+)?(?![a-z0-9])", result, re.I))
    if source_numbers - output_numbers:
        return False, "Missing source measurements or numeric values"
    for pattern in (r"\b(?:resolved|fully operational|fixed)\b",
                    r"\b(?:ordered|received|delivered)\b"):
        if re.search(pattern, result, re.I) and not re.search(pattern, src, re.I):
            return False, "Potential unsupported outcome"
    return True, "AI draft ready for MST review; not approved"


def _generate(item: sqlite3.Row) -> tuple[str, str, str]:
    notes = json.loads(item["source_json"])
    # Restrict to local Ollama, no remote API or outside network.
    data = json.dumps({
        "model": os.environ.get("IND3_OLLAMA_MODEL", MODEL),
        "stream": False, "keep_alive": "30m",
        "options": {"temperature": 0, "num_predict": 240},
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": "Reported asset: " + item["asset"] +
             "\nReported issue (context only): " + item["cause"] +
             "\nSOURCE REMARKS:\n" + "\n".join(
                f"Remark {i + 1}: {v}" for i, v in enumerate(notes)) +
             "\nReturn only the consolidated maintenance paragraph."}
        ]
    }).encode()
    req = urllib.request.Request("http://127.0.0.1:11434/api/chat", data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as response:
            payload = json.load(response)
        draft = str(payload.get("message", {}).get("content", "")).strip()
        good, reason = _verify(notes, draft)
        return (draft, "passed", "AI draft ready for MST review; not approved") if good else ("", "original", reason)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        return "", "original", "Ollama unavailable or request failed: " + type(exc).__name__

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
            draft, status, reason = _generate(row)
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

def statuses(shift_id: int) -> list[dict]:
    path = _db_path()
    ensure_table(path)
    with _connect(path) as c:
        return [dict(r) for r in c.execute("""SELECT wo_number,asset,cause,status,reason,approved,draft,source_json,source_hash
            FROM v3_ollama_drafts WHERE shift_id=? ORDER BY wo_number""",(shift_id,))]

def approved_for_report(shift_id: int) -> dict[str,str]:
    path = _db_path()
    ensure_table(path)
    with _connect(path) as c:
        return {r["wo_number"]:r["draft"] for r in c.execute(
            "SELECT wo_number,draft FROM v3_ollama_drafts WHERE shift_id=? AND status='passed' AND approved=1",
            (shift_id,))}


def review_draft(shift_id: int, wo_number: str, source_hash: str, *, accept: bool) -> bool:
    """Apply only the exact AI draft inspected by an MST.

    Source remarks remain unchanged in entries; rejected results retain the
    original. Excluded or manually edited WOs are never AI-approvable.
    """
    path = _db_path()
    ensure_table(path)
    with _connect(path) as c:
        entries = [dict(x) for x in c.execute(
            "SELECT * FROM entries WHERE shift_id=? AND excluded=0", (shift_id,))]
        metadata = {x["entry_id"]: dict(x) for x in c.execute(
            "SELECT * FROM v3_entry_meta WHERE shift_id=?", (shift_id,))}
    current = next((x for x in _group_sources(entries, metadata)
                    if x["wo"] == wo_number), None)
    if not current or any(metadata.get(x["id"], {}).get("mst_edited") for x in entries
                          if str(x.get("wo_number") or "") == wo_number):
        return False
    if _source_hash(current["asset"], current["cause"], current["notes"]) != source_hash:
        return False
    with _connect(path) as c:
        if accept:
            result = c.execute("""UPDATE v3_ollama_drafts SET approved=1,
                reason='Reviewed and accepted by MST'
                WHERE shift_id=? AND wo_number=? AND source_hash=?
                AND status='passed' AND length(trim(draft))>0""",
                (shift_id, wo_number, source_hash))
        else:
            result = c.execute("""UPDATE v3_ollama_drafts SET approved=0,
                status='original', draft='',
                reason='MST chose original technician remarks'
                WHERE shift_id=? AND wo_number=? AND source_hash=?
                AND status IN ('passed','original')""",
                (shift_id, wo_number, source_hash))
        return result.rowcount == 1

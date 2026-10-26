import v3_ollama_background as ai

def test_source_validation_preserves_measurements():
    okay, _ = ai._verify(["Pressure held at 110 psi, CODE304 active."],
                          "Pressure held at 110 psi; CODE304 remains active.")
    assert okay
    okay, reason = ai._verify(["Pressure held at 110 psi."], "Pressure held at 115 psi.")
    assert not okay
    assert "110 psi" in reason

def test_unsupported_rts_and_final_order_rejected():
    okay, reason = ai._verify(["Replacement part requested."], "Replacement part ordered. RTS.")
    assert not okay
    assert reason == "Unverified RTS"

def test_requested_work_not_accepted_as_ordered():
    okay, reason = ai._verify(["Replacement part requested."], "Replacement part ordered.")
    assert not okay
    assert reason == "Potential unsupported outcome"

def test_model_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("IND3_OLLAMA_REWRITE", raising=False)
    assert ai.enabled() is False

def test_extra_technical_details_trigger_review():
    okay, reason = ai._verify(["Installed MDR.C0004, braking 6ft."], "Installed MDR.C0004.")
    assert not okay
    assert "6ft" in reason

def test_invalid_model_claim_uses_original(monkeypatch):
    import json
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(ai.urllib.request,'urlopen',lambda *args,**kw:Response())
    monkeypatch.setattr(ai.json,'load',lambda _: {'message':{'content':'Part ordered. RTS.'}})
    draft,status,_=ai._generate({'source_json':json.dumps(['Part requested.']),'asset':'X','cause':'Part pending'})
    assert status=='original' and draft==''

def test_valid_model_output_auto_pass(monkeypatch):
    import json
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): return False
    monkeypatch.setattr(ai.urllib.request,'urlopen',lambda *args,**kw:Response())
    monkeypatch.setattr(ai.json,'load',lambda _: {'message':{'content':'Replaced right wheel; braking 5 ft.'}})
    draft,status,_=ai._generate({'source_json':json.dumps(['Replaced right wheel; braking 5 ft.']),'asset':'X','cause':'Issue'})
    assert status=='passed' and draft.startswith('Replaced')


def test_summary_instructions_are_never_passed():
    ok, reason=ai._verify(["Replaced two boxes of wire"],
                          "Consolidated maintenance actions: Replaced two boxes of wire.")
    assert not ok and "instruction" in reason.lower()

def test_cannot_drop_second_technician_observation():
    ok, reason=ai._verify(
        ["Replaced printer controller and tested power.",
         "Reset the HMI; fault remained, bagger was locked out."],
        "Replaced printer controller and tested power.")
    assert not ok and "source remark" in reason

def test_numeric_calibrations_cannot_be_truncated():
    ok, reason=ai._verify(
        ["Recalibrated fill head from 760 to 784"],
        "Recalibrated the fill head.")
    assert not ok and "technical" in reason.lower() or (not ok and "numeric" in reason.lower())

def test_auto_validated_ai_used_without_mst_approval(tmp_path):
    import json
    import db
    db.set_db_path(tmp_path/"validated_ai.db")
    db.init_db()
    sid = db.start_shift("2026-10-25","S4","MST Test")
    db.upsert_cmms_entry(sid,"AUTO-1","MDR0007","Roller trouble",
                         json.dumps(["Reseated O-ring belt and verified conveyor operation. RTS."]),
                         "Test Technician")
    ai.ensure_table(ai._db_path())
    with ai._connect(ai._db_path()) as c:
        entries = [dict(e) for e in c.execute("SELECT * FROM entries WHERE shift_id=?", (sid,))]
        groups = ai._group_sources(entries,{})
        assert len(groups)==1
        source = groups[0]
        stamp = ai._source_hash(source["asset"],source["cause"],source["notes"])
        c.execute("""INSERT INTO v3_ollama_drafts
            (shift_id,wo_number,source_hash,asset,cause,source_json,draft,status,approved)
            VALUES (?,?,?,?,?,?,?,'passed',0)""",
            (sid,"AUTO-1",stamp,source["asset"],source["cause"],json.dumps(source["notes"]),
             "Reseated O-ring belt and verified conveyor operation. RTS."))
    assert ai.approved_for_report(sid)=={
        "AUTO-1":"Reseated O-ring belt and verified conveyor operation. RTS."}
    assert ai.statuses(sid)[0]["status"]=="passed"


def test_old_passed_ai_without_source_evidence_restores_original(tmp_path):
    import json
    import db
    db.set_db_path(tmp_path/"old_ai.db")
    db.init_db()
    sid = db.start_shift("2026-10-25","S4","MST Test")
    db.upsert_cmms_entry(sid,"AUTO-2","BALER0005","Wire",
                         json.dumps(["Replaced two boxes of wire."]),
                         "Test Technician")
    ai.ensure_table(ai._db_path())
    with ai._connect(ai._db_path()) as c:
        entries = [dict(e) for e in c.execute("SELECT * FROM entries WHERE shift_id=?", (sid,))]
        item = ai._group_sources(entries,{})[0]
        stamp = ai._source_hash(item["asset"],item["cause"],item["notes"])
        c.execute("""INSERT INTO v3_ollama_drafts
            (shift_id,wo_number,source_hash,asset,cause,source_json,draft,status,approved)
            VALUES (?,?,?,?,?,?,?,'passed',1)""",
            (sid,"AUTO-2",stamp,item["asset"],item["cause"],json.dumps(item["notes"]),
             "Consolidated maintenance actions: Replaced two boxes of wire."))
    assert ai.approved_for_report(sid)=={}
    assert ai.statuses(sid)[0]["status"]=="original"
    assert "instruction" in ai.statuses(sid)[0]["reason"].lower()


def test_real_s5_prompt_echoes_fail_and_originals_survive():
    """Exact bad AI fragments captured from the October 25 S5 screenshot."""
    source=["Reset module and cleaned taper blade."]
    polluted=[
        "Consolidate duplicated actions even if described in different wording.",
        "Consolidate duplicated actions even if described in different wording. Reset module and cleaned taper blade.",
        "Consolidated maintenance: Reset module and cleaned taper blade.",
        "Consolidated maintenance paragraph: Reset module and cleaned taper blade.",
        "Motor Brush clean up is duplicated. Reset module and cleaned taper blade.",
        "Reset module and cleaned taper blade. Duplicate remark removed.",
        "Retain all distinct troubleshooting, repairs, measurements, codes, parts and statuses.",
        "Return ONE concise professional paragraph using ONLY the technician SOURCE remarks.",
    ]
    for bad in polluted:
        good,reason=ai._verify(source,bad)
        assert not good,(bad,reason)
        assert "instruction" in reason.casefold() or "duplicate" in reason.casefold(),(bad,reason)


def test_real_s5_rejects_new_work_even_when_other_source_terms_match():
    original=["Cleaned the taper blade and reset the module."]
    good,reason=ai._verify(original,"Cleaned the taper blade, replaced the fuse, and reset the module.")
    assert not good and "unsupported words" in reason.casefold()
    good,reason=ai._verify(original,"Cleaned the taper blade and reset the module.")
    assert good, reason


def test_automatic_validation_falls_back_on_prompt_echo_in_existing_passed_draft(tmp_path):
    import db
    import json
    db.set_db_path(tmp_path/"s5_leak.db")
    db.init_db()
    sid=db.start_shift("2026-10-25","S5","MST Test")
    db.upsert_cmms_entry(sid,"28027109","BAG0009","Excessive regen or internal voltage",
        json.dumps(["Checked battery plug connections and investigated regen alarm."]),
        "Test Technician")
    ai.ensure_table(ai._db_path())
    with ai._connect(ai._db_path()) as c:
        entries=[dict(e) for e in c.execute("SELECT * FROM entries WHERE shift_id=?", (sid,))]
        item=ai._group_sources(entries,{})[0]
        stamp=ai._source_hash(item["asset"],item["cause"],item["notes"])
        c.execute("""INSERT INTO v3_ollama_drafts
          (shift_id,wo_number,source_hash,asset,cause,source_json,draft,status,approved)
          VALUES (?,?,?,?,?,?,?,'passed',1)""",
          (sid,"28027109",stamp,item["asset"],item["cause"],json.dumps(item["notes"]),
           "Consolidate duplicated actions even if described in different wording."))
    assert ai.approved_for_report(sid)=={}
    status=ai.statuses(sid)[0]
    assert status["status"]=="original" and status["draft"]==""
    assert "instruction" in status["reason"].casefold()


def test_failed_local_ai_can_be_retried_without_reimport(tmp_path,monkeypatch):
    import json
    import db
    db.set_db_path(tmp_path/"ai_retry.db")
    db.init_db()
    sid=db.start_shift("2026-10-25","S5","MST Test")
    db.upsert_cmms_entry(sid,"RETRY-1","MDR.C0004","Belt slipped",
                         json.dumps(["Reseated the roller belt. RTS."]),
                         "Test Technician")
    monkeypatch.setenv("IND3_OLLAMA_REWRITE","1")
    monkeypatch.setattr(ai,"start_worker",lambda path:None)
    assert ai.queue_shift(sid)==1
    with ai._connect(ai._db_path()) as c:
        c.execute("UPDATE v3_ollama_drafts SET status='original', reason='Ollama offline' WHERE shift_id=?",(sid,))
    assert ai.queue_shift(sid)==0
    assert ai.queue_shift(sid,retry_original=True)==1
    assert ai.statuses(sid)[0]["status"]=="queued"


def test_ollama_worker_error_restores_original_and_processes_next_wo(tmp_path, monkeypatch):
    """An unexpected model error must never strand the rest as AI Processing."""
    import json
    import time
    import db
    db.set_db_path(tmp_path/"worker_errors.db");db.init_db()
    ai.ensure_table(ai._db_path())
    with ai._connect(ai._db_path()) as c:
        for wo in ("WO-ERROR","WO-NEXT"):
            c.execute("""INSERT INTO v3_ollama_drafts
                (shift_id,wo_number,source_hash,asset,cause,source_json,draft,status,approved)
                VALUES (1,?,?,?,'Issue',?,'','queued',0)""",
                (wo,wo,"M"+wo,json.dumps(["Replaced belt."])))
    def generate(item):
        if item["wo_number"]=="WO-ERROR":
            raise RuntimeError("simulated backend failure")
        return "Replaced belt.","passed","Automatic source validation passed"
    monkeypatch.setattr(ai,"_generate",generate)
    ai.start_worker(ai._db_path())
    end=time.monotonic()+5
    while time.monotonic()<end:
        with ai._connect(ai._db_path()) as c:
            states={r["wo_number"]:dict(r) for r in c.execute(
                "SELECT wo_number,status,reason FROM v3_ollama_drafts")}
        if all(item["status"] in ("original","passed") for item in states.values()):
            break
        time.sleep(.02)
    assert states["WO-ERROR"]["status"]=="original",states
    assert "RuntimeError" in states["WO-ERROR"]["reason"]
    assert states["WO-NEXT"]["status"]=="passed",states


def test_ai_poll_restarts_orphaned_writing_after_server_restart(tmp_path,monkeypatch):
    """A prior server crash can leave status='writing' forever without recovery."""
    import time
    import json
    import db
    monkeypatch.setenv("IND3_OLLAMA_REWRITE","1")
    db.set_db_path(tmp_path/"recovery.db");db.init_db()
    sid=db.start_shift("2026-10-25","S4","MST Test")
    db.upsert_cmms_entry(sid,"RESTART-1","MDR0001","Belt damaged",
                        json.dumps(["Replaced belt."]),"Tech A")
    ai.ensure_table(ai._db_path())
    with ai._connect(ai._db_path()) as c:
        entries=[dict(r) for r in c.execute("SELECT * FROM entries WHERE shift_id=?",(sid,))]
        group=ai._group_sources(entries,{})[0]
        c.execute("""INSERT INTO v3_ollama_drafts
            (shift_id,wo_number,source_hash,asset,cause,source_json,draft,status,approved)
            VALUES (?,?,?,?,?,?,'','writing',0)""",
            (sid,"RESTART-1",ai._source_hash(group["asset"],group["cause"],group["notes"]),
             group["asset"],group["cause"],json.dumps(group["notes"])))
    monkeypatch.setattr(ai,"_generate",lambda item:(
        "Replaced belt.","passed","Automatic source validation passed"))
    ai.statuses(sid)  # GET /v3/ai/drafts triggers pending-work recovery.
    end=time.monotonic()+5
    while time.monotonic()<end:
        progress=ai.statuses(sid)[0]
        if progress["status"]=="passed":
            break
        time.sleep(.03)
    assert progress["status"]=="passed", progress
    assert progress["approved"]==1
    assert ai.approved_for_report(sid)=={"RESTART-1":"Replaced belt."}


def test_missing_local_model_returns_explanatory_original(monkeypatch):
    import io
    import json
    import urllib.error
    def unavailable(*args,**kwargs):
        raise urllib.error.HTTPError("http://127.0.0.1:11434/api/chat",404,
                                    "Not Found",{},io.BytesIO(b""))
    monkeypatch.setattr(ai.urllib.request,"urlopen",unavailable)
    draft,status,reason=ai._generate({
        "source_json":json.dumps(["Replaced belt."]),
        "asset":"MDR0001","cause":"Belt damaged"})
    assert draft=="" and status=="original"
    assert "ollama list" in reason.lower()

from datetime import datetime, time, timedelta
from pathlib import Path
import re

import pytest
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient

import db
from v3_routes import create_v3_router

DIR = Path(__file__).resolve().parents[1]

@pytest.fixture
def app_client(tmp_path):
    db.set_db_path(tmp_path / 'test.db')
    db.init_db()
    sid = db.start_shift('2026-10-20', 'S2', 'Test MST')
    app=FastAPI()
    app.mount('/static', StaticFiles(directory=str(DIR/'static')), name='static')
    templates=Jinja2Templates(directory=str(DIR/'templates'))
    app.include_router(create_v3_router(
        templates,
        is_lead_authed=lambda req: req.cookies.get('lead_auth')=='test',
        can_manage_shift=lambda req, candidate: candidate == sid,
        resolve_my_shift=lambda req: db.get_shift(sid),
        shift_window=lambda shift,d:(datetime.combine(d,time(0)),datetime.combine(d+timedelta(days=1),time(0))),
        lead_redirect=lambda dest: RedirectResponse('/lead/login?next='+dest,status_code=303)
    ))
    with TestClient(app) as client:
        client.cookies.set('lead_auth','test')
        yield client,sid


def test_login_and_import_and_editor_and_review(app_client):
    client,sid=app_client
    resp=client.get('/v3')
    assert resp.status_code==200, resp.text[:500]
    assert 'Shift workspace' in resp.text
    sample=Path('/mnt/data/trial.xlsx')
    if not sample.exists(): pytest.skip('trial.xlsx unavailable')
    stage=client.post('/v3/import',data={'shift_id':sid},files={'file':('trial.xlsx',sample.read_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert stage.status_code==200,stage.text[:500]
    assert 'Who worked this shift?' in stage.text
    import re
    token=re.search(r'/v3/import/([a-f0-9]{32})/confirm',stage.text).group(1)
    selected=['Ssaku, Isaac','Melvin, Douglas','Bawi, Mang','Popoola, Waheed']
    confirmed=client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':selected},follow_redirects=True)
    assert confirmed.status_code==200,confirmed.text[:700]
    assert 'VOIDFILL1201' in confirmed.text
    assert db.get_pm_count(sid)==8
    entries=db.get_entries(sid)
    assert len(entries)==4,entries
    assert {e['tech_name'] for e in entries} <=set(selected)
    e=next(e for e in entries if e['unit']=='VOIDFILL1201')
    modal=client.get(f'/v3/happening/{e["id"]}?shift_id={sid}')
    assert modal.status_code==200
    assert 'reported_issue' in modal.text and 'v3-view-fields' in modal.text
    ed=client.post(f'/v3/happening/{e["id"]}/edit',data={'shift_id':sid,'reported_issue':'Head below MIN','cause_text':'','remarks_'+str(e['id']):'Recalibrated head; RTS'},follow_redirects=False)
    assert ed.status_code==303
    assert 'Recalibrated head' in db.get_entry(e['id'])['raw_remarks']
    # Re-uploading IDENTICAL Cetaris source must not flag MST-edited text.
    stage2=client.post('/v3/import',data={'shift_id':sid},files={'file':('trial.xlsx',sample.read_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    token2=re.search(r'/v3/import/([a-f0-9]{32})/confirm',stage2.text).group(1)
    confirmed2=client.post(f'/v3/import/{token2}/confirm',data={'shift_id':str(sid),'techs':selected},follow_redirects=True)
    assert '0 flagged for review' in confirmed2.text
    assert 'Recalibrated head; RTS' in db.get_entry(e['id'])['raw_remarks']
    # An actual Cetaris change must create a review item without overwriting.
    from openpyxl import load_workbook
    from io import BytesIO
    w=load_workbook(BytesIO(sample.read_bytes()))
    for row in list(w.active.iter_rows(min_row=2)):
        if str(row[0].value)=='28021671':
            row[39].value='Updated technician remark from revised Cetaris export'
            break
    buf=BytesIO();w.save(buf)
    changed=client.post('/v3/import',data={'shift_id':sid},files={'file':('revised.xlsx',buf.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    token3=re.search(r'/v3/import/([a-f0-9]{32})/confirm',changed.text).group(1)
    confirmed3=client.post(f'/v3/import/{token3}/confirm',data={'shift_id':str(sid),'techs':selected},follow_redirects=True)
    assert 'Review update' in confirmed3.text
    assert 'Recalibrated head; RTS' in db.get_entry(e['id'])['raw_remarks']
    keep=client.post(f'/v3/happening/{e["id"]}/review',data={'shift_id':sid,'decision':'reject'},follow_redirects=False)
    assert keep.status_code==303
    assert 'Recalibrated head; RTS' in db.get_entry(e['id'])['raw_remarks']
    preview=client.get('/v3/preview',params={'shift_id':sid})
    assert preview.status_code==200
    assert 'Recalibrated head; RTS' in preview.text


def test_shift_ownership_and_unauthenticated_access(app_client):
    client,sid=app_client
    assert client.get('/v3',params={'shift_id':sid+999},follow_redirects=False).status_code==303
    client.cookies.clear()
    assert client.get('/v3',follow_redirects=False).status_code==303
    assert client.post('/v3/manual',data={'shift_id':sid,'unit':'M1','issue':'x'},follow_redirects=False).status_code==401


def test_changing_selected_techs_filters_pm_and_happenings(app_client):
    client,sid=app_client
    sample=Path('/mnt/data/trial.xlsx')
    if not sample.exists(): pytest.skip('trial.xlsx unavailable')
    def do_import(selected):
        staged=client.post('/v3/import',data={'shift_id':sid},files={'file':('trial.xlsx',sample.read_bytes(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
        token=re.search(r'/v3/import/([a-f0-9]{32})/confirm',staged.text).group(1)
        return client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':selected},follow_redirects=True)
    first=do_import(['Ssaku, Isaac','Melvin, Douglas','Bawi, Mang','Popoola, Waheed'])
    assert db.get_pm_count(sid)==8
    second=do_import(['Ssaku, Isaac'])
    assert db.get_pm_count(sid)==2
    assert len([e for e in db.get_entries(sid) if not e['excluded']])==1
    assert 'VOIDFILL1201' in second.text


def test_reselect_tech_and_closed_pm_replacement(app_client):
    """Changing selection restores only auto-hidden entries; reopened PMs disappear."""
    import json, uuid
    client, sid = app_client
    from v3_cetaris import parse_excel
    source=Path('/mnt/data/trial.xlsx')
    if not source.exists(): pytest.skip('trial.xlsx unavailable')
    records=parse_excel(source.read_bytes())
    client.get('/v3')  # Initializes V3 sidecar tables in the test DB.
    def confirm(selected, rows=records):
        token=uuid.uuid4().hex
        with db._conn() as c:
            c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                      (token,sid,'test.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps(rows)))
        response=client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':selected},follow_redirects=False)
        return response
    selected=['Ssaku, Isaac','Melvin, Douglas','Bawi, Mang','Popoola, Waheed']
    assert confirm(selected).status_code==303
    assert db.get_pm_count(sid)==8
    entry=next(e for e in db.get_entries(sid) if e['tech_name']=='Ssaku, Isaac')
    assert entry['excluded']==0
    assert confirm(selected[1:]).status_code==303
    assert db.get_entry(entry['id'])['excluded']==1
    assert db.get_pm_count(sid)==6
    assert confirm(selected).status_code==303
    assert db.get_entry(entry['id'])['excluded']==0
    assert db.get_pm_count(sid)==8
    # When a PM is reopened, no longer count it as a completed PM.
    changed=[{**r,'status':'Open'} if r['rfr']=='Preventive Maintenance' and r['tech_name']=='Ssaku, Isaac' else r for r in records]
    assert confirm(selected,changed).status_code==303
    assert db.get_pm_count(sid)==6


def test_loto_with_missing_keys_not_verified(app_client):
    client,sid=app_client
    result=client.post('/v3/loto',data={'shift_id':sid,'verified':'1','missing_keys':'Key #2'})
    assert result.status_code==200
    assert db.get_loto(sid)['loto_verified']==0
    assert db.get_loto(sid)['loto_missing_keys']=='Key #2'


def test_pm_upload_lists_file_and_parses_pending_rows(app_client):
    client,sid=app_client
    csv_bytes=(
        b'WO Number,WO Date,PM Due Date,Promise Date,Asset #\n'
        b'WO123,10/01/2026,10/05/2026,10/06/2026,VOIDFILL0201\n'
    )
    upload=client.post('/v3/pm-upload',data={'shift_id':sid},
                        files={'pm_file':('open_pms.csv',csv_bytes,'text/csv')})
    assert upload.status_code==200,upload.text[:500]
    assert 'open_pms.csv' in upload.text
    assert 'WO123' in upload.text
    assert 'VOIDFILL0201' in upload.text
    assert len(db.get_pending_pms(sid))==1

    listing=client.get(f'/v3/pm-files?shift_id={sid}')
    assert listing.status_code==200
    assert 'open_pms.csv' in listing.text

    file_id=db.get_pm_files(sid)[0]['id']
    download=client.get(f'/v3/pm-file/{file_id}/download?shift_id={sid}')
    assert download.status_code==200
    assert download.content==csv_bytes

    deleted=client.post(f'/v3/pm-file/{file_id}/delete',data={'shift_id':sid})
    assert deleted.status_code==200
    assert db.get_pm_files(sid)==[]
    assert db.get_pending_pms(sid)==[]


def test_pm_upload_rejects_disallowed_extension(app_client):
    client,sid=app_client
    rejected=client.post('/v3/pm-upload',data={'shift_id':sid},
                          files={'pm_file':('malware.exe',b'nope','application/octet-stream')})
    assert rejected.status_code==400
    assert db.get_pm_files(sid)==[]


def test_unfamiliar_rfr_imports_and_reopened_wo_is_excluded(app_client):
    """Full route/database path: new RFR included, then reopened excluded safely."""
    import json, uuid
    client,sid=app_client
    client.get('/v3')
    base={"wo_number":"98765","tech_name":"Ssaku, Isaac","asset_id":"ASSET987",
          "status":"Closed","rfr":"Safety Inspection","reported_issue":"Check guarding",
          "cause":"","remarks":"Guard inspected","start_at":"2026-10-20T18:00:00",
          "closed_at":"","repair_class":"","pm_code":"","last_shift":""}
    def send(rows):
        token=uuid.uuid4().hex
        with db._conn() as c:
            c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                      (token,sid,'sample.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps(rows)))
        return client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':['Ssaku, Isaac']},follow_redirects=True)
    first=send([base])
    assert first.status_code==200
    assert 'Safety Inspection' in first.text
    assert 'ASSET987' in first.text
    imported=next(e for e in db.get_entries(sid) if e['wo_number']=='98765')
    assert imported['excluded']==0
    second=send([{**base,'status':'Open'}])
    assert second.status_code==200
    assert db.get_entry(imported['id'])['excluded']==1
    third=send([base])
    assert third.status_code==200
    assert db.get_entry(imported['id'])['excluded']==0


def test_breakdown_wo_tracked_as_oos_then_rts_decision(app_client):
    """Importing an in-process Breakdown WO marks the asset OOS via the
    shared oos_units table; marking it Completed surfaces an explicit RTS
    decision instead of silently assuming the unit is back up."""
    import json, uuid
    client,sid=app_client
    client.get('/v3')
    base={"wo_number":"55001","tech_name":"Ssaku, Isaac","asset_id":"VOIDFILL0500",
          "status":"In Process","rfr":"Breakdown","reported_issue":"Conveyor jam",
          "cause":"","remarks":"Working it","start_at":"2026-10-20T18:00:00",
          "closed_at":"","repair_class":"Unplanned","pm_code":"","last_shift":"S2"}
    def send(rows):
        token=uuid.uuid4().hex
        with db._conn() as c:
            c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                      (token,sid,'sample.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps(rows)))
        return client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':['Ssaku, Isaac']},follow_redirects=True)
    first=send([base])
    assert first.status_code==200,first.text[:500]
    assert 'VOIDFILL0500' in db.get_oos_units()
    workspace=client.get(f'/v3?shift_id={sid}')
    assert 'No breakdown WOs awaiting a decision.' in workspace.text
    completed=send([{**base,'status':'Completed'}])
    assert completed.status_code==200
    workspace=client.get(f'/v3?shift_id={sid}')
    assert 'WO #55001' in workspace.text
    assert 'VOIDFILL0500' in db.get_oos_units(), 'still OOS until an explicit decision is made'
    decided=client.post('/v3/breakdown/decide',data={'shift_id':str(sid),'wo_number':'55001','decision':'rts'},follow_redirects=True)
    assert decided.status_code==200
    assert 'VOIDFILL0500' not in db.get_oos_units()


def test_import_review_surfaces_skipped_wo_and_can_be_included(app_client):
    """A WO closed outside this shift's window used to vanish into an
    aggregate count ("Skipped 1 outside this shift's time window") with no
    way to see which WO or act on it. It must now show up in the Needs
    Review panel with its real detail, and an MST must be able to pull it
    into the shift explicitly."""
    import json, uuid
    client,sid=app_client
    client.get('/v3')
    # 1pm Tuesday sits in the genuine S1(5:30-16:00)/S2(12:00-22:30) overlap.
    # app_client's roster is empty, so v3_split.py's engine can't disambiguate
    # from roster/continuity alone -- a real "needs an MST shift call" case.
    row=dict(wo_number="77001",tech_name="Ssaku, Isaac",asset_id="TSP0009",status="Closed",
             rfr="Process Interruption",reported_issue="Belt jam",cause="",remarks="Cleared jam",
             start_at="2026-10-20T13:00:00",closed_at="2026-10-20T13:30:00",
             repair_class="Unplanned",pm_code="",last_shift="S2")
    token=uuid.uuid4().hex
    with db._conn() as c:
        c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                  (token,sid,'sample.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps([row])))
    resp=client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':['Ssaku, Isaac']},follow_redirects=True)
    assert resp.status_code==200,resp.text[:500]
    assert 'see Needs Review for details' in resp.text

    workspace=client.get(f'/v3?shift_id={sid}')
    assert 'WO #77001' in workspace.text
    assert 'TSP0009' in workspace.text
    assert db.get_entries(sid)==[], 'must not silently appear as a happening before ownership is resolved'

    import v3_import_review
    [item]=v3_import_review.pending(db,sid)
    assert item['kind']=='ownership'
    assigned=client.post(f'/v3/import-review/{item["id"]}/assign-shift',
                         data={'shift_id':str(sid),'shift_type':'S2'},follow_redirects=True)
    assert assigned.status_code==200
    assert v3_import_review.pending(db,sid)==[], 'resolved item must drop off the pending list'
    assert db.get_entries(sid)==[], 'assign_shift only records the decision -- it never inserts an entry itself'

    # The standing decision is honored on the NEXT import of this WO, by
    # whichever shift re-uploads it -- the MST is never asked twice.
    token2=uuid.uuid4().hex
    with db._conn() as c:
        c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                  (token2,sid,'sample.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps([row])))
    resp2=client.post(f'/v3/import/{token2}/confirm',data={'shift_id':str(sid),'techs':['Ssaku, Isaac']},follow_redirects=True)
    assert resp2.status_code==200,resp2.text[:500]
    entries=db.get_entries(sid)
    assert len(entries)==1 and entries[0]['unit']=='TSP0009'


def test_import_review_dismiss_does_not_create_entry(app_client):
    client,sid=app_client
    client.get('/v3')
    import json, uuid, v3_import_review
    # 1pm Tuesday: genuine S1/S2 overlap, unresolvable without roster data --
    # same scenario as the include/assign-shift test above.
    row=dict(wo_number="77002",tech_name="Ssaku, Isaac",asset_id="TSP0010",status="Closed",
             rfr="Process Interruption",reported_issue="False alarm",cause="",remarks="N/A",
             start_at="2026-10-20T13:00:00",closed_at="2026-10-20T13:30:00",
             repair_class="Unplanned",pm_code="",last_shift="S2")
    token=uuid.uuid4().hex
    with db._conn() as c:
        c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                  (token,sid,'sample.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps([row])))
    client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':['Ssaku, Isaac']},follow_redirects=True)
    [item]=v3_import_review.pending(db,sid)
    dismissed=client.post(f'/v3/import-review/{item["id"]}/dismiss',data={'shift_id':str(sid)},follow_redirects=True)
    assert dismissed.status_code==200
    assert db.get_entries(sid)==[]
    assert v3_import_review.pending(db,sid)==[]


def test_s4_s5_split_shift_ownership(tmp_path):
    """A WO whose technician work-start time falls squarely in S4's window
    must not be credited to an S5 import of the same weekend's Cetaris
    export; a work-start inside the genuine 17:30-18:00 S4/S5 overlap, with
    no roster to disambiguate, is held out for an explicit MST shift call
    instead of being guessed.
    """
    import json, uuid
    db.set_db_path(tmp_path / 'test_split.db')
    db.init_db()
    # 2026-10-23 is a real Friday -- v3_split.py's engine keys its S4/S5
    # schedule off actual weekday, so the test date must really be a
    # Fri/Sat/Sun or the whole scenario silently resolves to S1/S2/S3 instead.
    s5_sid=db.start_shift('2026-10-23','S5','Test MST S5')
    app=FastAPI()
    app.mount('/static', StaticFiles(directory=str(DIR/'static')), name='static')
    templates=Jinja2Templates(directory=str(DIR/'templates'))
    app.include_router(create_v3_router(
        templates,
        is_lead_authed=lambda req: req.cookies.get('lead_auth')=='test',
        can_manage_shift=lambda req, candidate: candidate == s5_sid,
        resolve_my_shift=lambda req: db.get_shift(s5_sid),
        shift_window=lambda shift,d:(datetime.combine(d,time(0)),datetime.combine(d+timedelta(days=1),time(0))),
        lead_redirect=lambda dest: RedirectResponse('/lead/login?next='+dest,status_code=303)
    ))
    client=TestClient(app)
    client.cookies.set('lead_auth','test')
    client.get('/v3')
    # Unambiguously S4: 10am Friday is nowhere near S5's 17:30 start.
    row_for_s4=dict(wo_number="66001",tech_name="Ssaku, Isaac",asset_id="M1",status="Closed",
                     rfr="Process Interruption",reported_issue="Fixed",cause="",remarks="Done",
                     start_at="2026-10-23T10:00:00",closed_at="2026-10-23T10:30:00",
                     repair_class="",pm_code="",last_shift="S4")
    # Genuinely ambiguous: 5:45pm Friday is inside the real S4(5:30-18:00)/
    # S5(17:30-06:00) overlap, this test's router has no roster data to break
    # the tie, and this is a DIFFERENT technician with no other unambiguous
    # work-start of their own this file -- so there's no continuity evidence
    # to resolve it either (unlike row_for_s4's own tech, whose earlier 10am
    # S4 start would otherwise "cover" a later ambiguous entry of theirs).
    ambiguous_row=dict(row_for_s4,wo_number="66002",tech_name="Lin, Priya",
                       start_at="2026-10-23T17:45:00",closed_at="")
    def send(sid,rows):
        token=uuid.uuid4().hex
        with db._conn() as c:
            c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                      (token,sid,'sample.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps(rows)))
        return client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':['Ssaku, Isaac','Lin, Priya']},follow_redirects=True)
    resp=send(s5_sid,[row_for_s4,ambiguous_row])
    assert resp.status_code==200,resp.text[:500]
    s5_entries=db.get_entries(s5_sid)
    assert not any(e['wo_number']=='66001' for e in s5_entries), 'work-start during S4 window, must not count toward S5'
    assert not any(e['wo_number']=='66002' for e in s5_entries), 'ambiguous overlap start must not be guessed'
    assert 'explicit shift ownership call' in resp.text

    import v3_import_review
    [item]=v3_import_review.pending(db,s5_sid)
    assert item['kind']=='ownership' and item['wo_number']=='66002'


def test_cross_shift_duplicate_wo_is_blocked_not_double_credited(tmp_path):
    """The same completed WO must never be credited to two different
    shifts. If a WO already belongs to another shift's entries (or PMs),
    a later import that would also claim it is blocked with a clear
    message, never silently overwritten or double-counted.
    """
    import json, uuid
    db.set_db_path(tmp_path / 'test_cross_shift.db')
    db.init_db()
    s2_sid=db.start_shift('2026-10-20','S2','Test MST S2')
    s3_sid=db.start_shift('2026-10-19','S3','Test MST S3')
    # Seed WO 71001 as already belonging to S3 -- bypassing the import
    # pipeline entirely, since the point of this test is the cross-shift
    # guard itself, not re-deriving S3 ownership from scratch.
    db.upsert_cmms_entry(s3_sid,'71001','BALER0002','Jam','[]','Ray Stamps')
    app=FastAPI()
    app.mount('/static', StaticFiles(directory=str(DIR/'static')), name='static')
    templates=Jinja2Templates(directory=str(DIR/'templates'))
    app.include_router(create_v3_router(
        templates,
        is_lead_authed=lambda req: req.cookies.get('lead_auth')=='test',
        can_manage_shift=lambda req, candidate: candidate in (s2_sid,s3_sid),
        resolve_my_shift=lambda req: db.get_shift(s2_sid),
        shift_window=lambda shift,d:(datetime.combine(d,time(0)),datetime.combine(d+timedelta(days=1),time(0))),
        lead_redirect=lambda dest: RedirectResponse('/lead/login?next='+dest,status_code=303)
    ))
    client=TestClient(app)
    client.cookies.set('lead_auth','test')
    client.get('/v3')
    # Clean S2-only work-start (6pm Tuesday) for the SAME wo_number -- would
    # normally import cleanly into S2 on its own merits.
    row=dict(wo_number="71001",tech_name="Ray Stamps",asset_id="BALER0002",status="Closed",
             rfr="Process Interruption",reported_issue="Jam",cause="",remarks="Cleared",
             start_at="2026-10-20T18:00:00",closed_at="",repair_class="",pm_code="",last_shift="S2")
    token=uuid.uuid4().hex
    with db._conn() as c:
        c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                  (token,s2_sid,'sample.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps([row])))
    resp=client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(s2_sid),'techs':['Ray Stamps']},follow_redirects=True)
    assert resp.status_code==200
    assert 'Import blocked to prevent double-credit' in resp.text
    assert 'WO 71001' in resp.text
    # The original S3 entry must be untouched -- no overwrite, no new S2 entry.
    assert any(e['wo_number']=='71001' for e in db.get_entries(s3_sid))
    assert not any(e['wo_number']=='71001' for e in db.get_entries(s2_sid))


def _two_shift_router(tmp_path,sid_a,sid_b):
    """Shared scaffolding for cross-shift duplicate tests below: one router
    that can manage both shifts, mirroring an MST session that legitimately
    has access to adjacent shifts (or a stale/retried request)."""
    app=FastAPI()
    app.mount('/static', StaticFiles(directory=str(DIR/'static')), name='static')
    templates=Jinja2Templates(directory=str(DIR/'templates'))
    app.include_router(create_v3_router(
        templates,
        is_lead_authed=lambda req: req.cookies.get('lead_auth')=='test',
        can_manage_shift=lambda req, candidate: candidate in (sid_a,sid_b),
        resolve_my_shift=lambda req: db.get_shift(sid_a),
        shift_window=lambda shift,d:(datetime.combine(d,time(0)),datetime.combine(d+timedelta(days=1),time(0))),
        lead_redirect=lambda dest: RedirectResponse('/lead/login?next='+dest,status_code=303)
    ))
    client=TestClient(app)
    client.cookies.set('lead_auth','test')
    client.get('/v3')
    return client


def _stage_and_confirm(client,sid,row,techs):
    import json, uuid
    token=uuid.uuid4().hex
    with db._conn() as c:
        c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                  (token,sid,'sample.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps([row])))
    return client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':techs},follow_redirects=True)


def _pm_rows(shift_id):
    with db._conn() as c:
        return c.execute("SELECT wo_number FROM pm_wos WHERE shift_id=?",(shift_id,)).fetchall()


def test_pm_already_credited_to_s3_blocks_s4_reimport(tmp_path):
    """A PM WO already recorded against S3 must never also be credited to a
    later S4 import of the same WO number -- PM-vs-PM, not just PM-vs-entries."""
    db.set_db_path(tmp_path / 'test_pm_s3_s4.db')
    db.init_db()
    s3_sid=db.start_shift('2026-10-22','S3','Test MST S3')
    s4_sid=db.start_shift('2026-10-23','S4','Test MST S4')
    db.upsert_pm_wo(s3_sid,'81001','BALER0003','Stamps, Ray')
    client=_two_shift_router(tmp_path,s3_sid,s4_sid)
    # Clean S4-only work-start, same WO number, PM-coded -- would otherwise
    # import cleanly into S4 on its own merits.
    row=dict(wo_number="81001",tech_name="Stamps, Ray",asset_id="BALER0003",status="Closed",
             rfr="PM",reported_issue="Scheduled PM",cause="",remarks="PM completed",
             start_at="2026-10-23T09:00:00",closed_at="",repair_class="Planned",
             pm_code="PM1",last_shift="S4")
    resp=_stage_and_confirm(client,s4_sid,row,['Stamps, Ray'])
    assert resp.status_code==200
    assert 'Import blocked to prevent double-credit' in resp.text
    assert 'WO 81001' in resp.text
    assert any(p['wo_number']=='81001' for p in _pm_rows(s3_sid))
    assert not _pm_rows(s4_sid)


def test_pm_already_credited_to_s4_blocks_s5_reimport(tmp_path):
    """Same guard, the Friday-S4-into-Friday-S5 boundary this whole project
    is centered on: a PM already in S4 must not also land in S5."""
    db.set_db_path(tmp_path / 'test_pm_s4_s5.db')
    db.init_db()
    s4_sid=db.start_shift('2026-10-23','S4','Test MST S4')
    s5_sid=db.start_shift('2026-10-23','S5','Test MST S5')
    db.upsert_pm_wo(s4_sid,'81002','FLEX0009','Nusbaum, Harry')
    client=_two_shift_router(tmp_path,s4_sid,s5_sid)
    row=dict(wo_number="81002",tech_name="Nusbaum, Harry",asset_id="FLEX0009",status="Closed",
             rfr="PM",reported_issue="Scheduled PM",cause="",remarks="PM completed",
             start_at="2026-10-23T20:00:00",closed_at="",repair_class="Planned",
             pm_code="PM1",last_shift="S5")
    resp=_stage_and_confirm(client,s5_sid,row,['Nusbaum, Harry'])
    assert resp.status_code==200
    assert 'Import blocked to prevent double-credit' in resp.text
    assert 'WO 81002' in resp.text
    assert _pm_rows(s4_sid) and not _pm_rows(s5_sid)


def test_multi_technician_wo_blocked_regardless_of_which_tech_reimports(tmp_path):
    """A WO already recorded (under one technician) in another shift must
    still be blocked even when a DIFFERENT technician on the same WO is the
    one selected for the later import -- the guard keys on wo_number, not
    the specific technician/shift combination that happened to claim it."""
    db.set_db_path(tmp_path / 'test_multi_tech.db')
    db.init_db()
    s3_sid=db.start_shift('2026-10-22','S3','Test MST S3')
    s4_sid=db.start_shift('2026-10-23','S4','Test MST S4')
    db.upsert_cmms_entry(s3_sid,'81003','WAV0012','Panel fault','[]','Stamps, Ray')
    client=_two_shift_router(tmp_path,s3_sid,s4_sid)
    # A DIFFERENT technician (Johnson, Damond) also worked this same WO,
    # selected for S4's import -- still must not double-credit.
    row=dict(wo_number="81003",tech_name="Johnson, Damond",asset_id="WAV0012",status="Closed",
             rfr="Process Interruption",reported_issue="Panel fault",cause="",remarks="Helped diagnose",
             start_at="2026-10-23T09:00:00",closed_at="",repair_class="",pm_code="",last_shift="S4")
    resp=_stage_and_confirm(client,s4_sid,row,['Johnson, Damond'])
    assert resp.status_code==200
    assert 'Import blocked to prevent double-credit' in resp.text
    assert not any(e['wo_number']=='81003' for e in db.get_entries(s4_sid))


def test_repeated_upload_of_same_shift_same_wo_does_not_double_count(tmp_path):
    """Re-uploading the SAME file (or a later export containing the same
    already-imported WO) to the SAME shift must be idempotent -- reported as
    'unchanged', never a second entry or inflated happening count."""
    db.set_db_path(tmp_path / 'test_repeat_upload.db')
    db.init_db()
    s2_sid=db.start_shift('2026-10-20','S2','Test MST S2')
    client=_two_shift_router(tmp_path,s2_sid,s2_sid)
    row=dict(wo_number="81004",tech_name="Ssaku, Isaac",asset_id="M2360",status="Closed",
             rfr="Process Interruption",reported_issue="Cab smell",cause="",remarks="Inspected",
             start_at="2026-10-20T18:00:00",closed_at="",repair_class="",pm_code="",last_shift="S2")
    first=_stage_and_confirm(client,s2_sid,row,['Ssaku, Isaac'])
    assert first.status_code==200
    assert 'Imported 1 new technician/WO entries' in first.text
    entries_after_first=db.get_entries(s2_sid)
    assert len([e for e in entries_after_first if e['wo_number']=='81004'])==1

    second=_stage_and_confirm(client,s2_sid,row,['Ssaku, Isaac'])
    assert second.status_code==200
    assert 'Imported 0 new technician/WO entries; 1 unchanged' in second.text
    entries_after_second=db.get_entries(s2_sid)
    assert len([e for e in entries_after_second if e['wo_number']=='81004'])==1, \
        'repeated upload of the same WO must never create a second entry'
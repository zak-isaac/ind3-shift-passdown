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
          "cause":"","remarks":"Guard inspected","start_at":"2026-10-20T13:00:00",
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
          "cause":"","remarks":"Working it","start_at":"2026-10-20T13:00:00",
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


def test_s4_s5_split_shift_ownership(tmp_path):
    """A WO closed inside S4's window must not be credited to an S5 import of
    the same 24-hour Cetaris export; an ambiguous close time is held out and
    surfaced to the MST instead of being guessed."""
    import json, uuid
    db.set_db_path(tmp_path / 'test_split.db')
    db.init_db()
    s5_sid=db.start_shift('2026-10-20','S5','Test MST S5')
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
    row_for_s4=dict(wo_number="66001",tech_name="Ssaku, Isaac",asset_id="M1",status="Closed",
                     rfr="Process Interruption",reported_issue="Fixed",cause="",remarks="Done",
                     start_at="2026-10-20T10:00:00",closed_at="2026-10-20T10:30:00",
                     repair_class="",pm_code="",last_shift="S4")
    ambiguous_row=dict(row_for_s4,wo_number="66002",closed_at="")
    def send(sid,rows):
        token=uuid.uuid4().hex
        with db._conn() as c:
            c.execute('INSERT INTO v3_import_batches(token,shift_id,filename,created_at,rows_json) VALUES (?,?,?,?,?)',
                      (token,sid,'sample.xlsx',datetime.now().isoformat(timespec='seconds'),json.dumps(rows)))
        return client.post(f'/v3/import/{token}/confirm',data={'shift_id':str(sid),'techs':['Ssaku, Isaac']},follow_redirects=True)
    resp=send(s5_sid,[row_for_s4,ambiguous_row])
    assert resp.status_code==200,resp.text[:500]
    s5_entries=db.get_entries(s5_sid)
    assert not any(e['wo_number']=='66001' for e in s5_entries), 'closed during S4 window, must not count toward S5'
    assert not any(e['wo_number']=='66002' for e in s5_entries), 'ambiguous close time must not be guessed'
    assert 'explicit S4/S5 ownership call' in resp.text

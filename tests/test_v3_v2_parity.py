"""V3 keeps V2 features working while changing only the Cetaris work-order import."""
from datetime import datetime, date, time, timedelta
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
import db
from v3_routes import create_v3_router

ROOT=Path(__file__).resolve().parents[1]

def test_mst_and_technician_both_start_and_resume_independent_shifts(tmp_path):
    db.set_db_path(tmp_path/'isolated.db');db.init_db()
    roster={
       '1001':{'display_name':'First MST','is_mst':True,'is_manager':False,'shift':'S2'},
       '2002':{'display_name':'Second Tech','is_mst':False,'is_manager':False,'shift':'S3'},
       '8972':{'display_name':'Manager','is_mst':False,'is_manager':True,'shift':'ALL'},
    }
    app=FastAPI()
    app.mount('/static',StaticFiles(directory=str(ROOT/'static')),name='static')
    app.include_router(create_v3_router(
       templates=Jinja2Templates(directory=str(ROOT/'templates')),
       is_lead_authed=lambda request: False,
       can_manage_shift=lambda request,sid: False,
       resolve_my_shift=lambda request: None,
       shift_window=lambda shift,d:(datetime.combine(d,time(0)), datetime.combine(d+timedelta(days=1),time(0))),
       lead_redirect=lambda destination: None,
       get_mst_roster=lambda:roster,
       start_eligibility=lambda person,shift,now:(person.get('shift')==shift,False,'Wrong assignment'),
       allowed_shift_days={'S2':{1},'S3':{1}},
       now_func=lambda:datetime(2026,10,20,21,0),
       get_v2_cookie_value=lambda:'v2-lead-auth',
    ))
    with TestClient(app) as browser:
        entry=browser.get('/v3/login')
        assert entry.status_code==200
        assert 'walmart-logo-white.svg' in entry.text  # logo asset actually used by V3 login
        assert 'associate number' in entry.text.lower()  # form still asks for an associate number
        assert 'name="pin"' not in entry.text
        deny=browser.post('/v3/login',data={'associate_id':'9999','mode':'start','shift_type':'S2','shift_date':'2026-10-20'})
        assert 'Associate number not found' in deny.text
        # 8972 doubles as a fallback master code (see v3_routes._FALLBACK_MASTER_CODES),
        # so a manager logging in with their own associate number is routed into the
        # management shift selector rather than rejected.
        master=browser.post('/v3/login',data={'associate_id':'8972','mode':'start','shift_type':'S2','shift_date':'2026-10-20'})
        assert "getElementById('master-shift-dialog').showModal();" in master.text
        first=browser.post('/v3/login',data={'associate_id':'1001','mode':'start','shift_type':'S2','shift_date':'2026-10-20'},follow_redirects=False)
        assert first.status_code==303
        sid1=db.get_active_shifts()[0]['id']
        assert db.get_shift(sid1)['opened_by_role']=='mst'
        assert db.get_shift(sid1)['opened_by_assoc']=='1001'
        operations=browser.get(f'/v3/operations?shift_id={sid1}')
        assert operations.status_code==200
        assert str(sid1) in operations.text  # native V3 operations page rendered for this shift
        # Non-MST associate cannot take over a shift started by someone else.
        browser.get('/v3/logout')
        wrong=browser.post('/v3/login',data={'associate_id':'2002','mode':'resume','shift_id':sid1})
        assert 'not started by your associate number' in wrong.text
        second=browser.post('/v3/login',data={'associate_id':'2002','mode':'start','shift_type':'S3','shift_date':'2026-10-20'},follow_redirects=False)
        assert second.status_code==303
        sid2=next(s['id'] for s in db.get_active_shifts() if s['shift_type']=='S3')
        assert db.get_shift(sid2)['opened_by_role']=='tech'
        assert db.get_shift(sid2)['opened_by_assoc']=='2002'
        assert browser.get(f'/v3/operations?shift_id={sid2}').status_code==200
        assert browser.get(f'/v3/operations?shift_id={sid1}').status_code==403
        assert browser.post('/v3/manual',data={'shift_id':sid1,'unit':'M1','issue':'cross-shift'},follow_redirects=False).status_code==403
        # Re-posting 'start' for an associate who already owns a live shift is
        # idempotent -- it just redirects back into their own existing shift
        # rather than erroring (the "already active" rejection only fires for
        # a *different* associate trying to claim an in-use shift code).
        duplicate=browser.post('/v3/login',data={'associate_id':'2002','mode':'start','shift_type':'S3','shift_date':'2026-10-20'},follow_redirects=False)
        assert duplicate.status_code==303
        assert f'shift_id={sid2}' in duplicate.headers['location']
        # Restore the original session and verify that it still sees only sid1.
        browser.get('/v3/logout')
        own=browser.post('/v3/login',data={'associate_id':'1001','mode':'resume','shift_id':sid1},follow_redirects=False)
        assert own.status_code==303
        assert browser.get(f'/v3/operations?shift_id={sid1}').status_code==200
        assert browser.get(f'/v3/operations?shift_id={sid2}').status_code==403


def test_personal_master_credential_gets_manager_rights_under_own_identity(tmp_path):
    """A real associate number that's also a configured master credential
    (e.g. Isaac's own 2759, main.py's _MASTER_CODES -- 'same rights as 8972')
    must be routed into the management shift selector just like 8972, and be
    able to pick up ANY active shift -- not just ones they personally
    started -- while keeping their own associate number as the attribution,
    not masquerading as the synthetic 'Manager' identity.

    Regression coverage for: typing a personal master number into the normal
    login field used to fall through to the ordinary-associate path and fail
    with "This shift was not started by your associate number."
    """
    db.set_db_path(tmp_path/'isolated_master.db');db.init_db()
    roster={
       '1001':{'display_name':'First MST','is_mst':True,'is_manager':False,'shift':'S2'},
       '2759':{'display_name':'Isaac S','is_mst':False,'is_manager':False,'shift':'S5'},
       '8972':{'display_name':'Manager','is_mst':False,'is_manager':True,'shift':'ALL'},
    }
    app=FastAPI()
    app.mount('/static',StaticFiles(directory=str(ROOT/'static')),name='static')
    app.include_router(create_v3_router(
       templates=Jinja2Templates(directory=str(ROOT/'templates')),
       is_lead_authed=lambda request: False,
       can_manage_shift=lambda request,sid: False,
       resolve_my_shift=lambda request: None,
       shift_window=lambda shift,d:(datetime.combine(d,time(0)), datetime.combine(d+timedelta(days=1),time(0))),
       lead_redirect=lambda destination: None,
       get_mst_roster=lambda:roster,
       verify_master_code=lambda code: code.strip() in ('8972','2759'),
       start_eligibility=lambda person,shift,now:(person.get('shift')==shift,False,'Wrong assignment'),
       allowed_shift_days={'S2':{1},'S3':{1}},
       now_func=lambda:datetime(2026,10,20,21,0),
       get_v2_cookie_value=lambda:'v2-lead-auth',
    ))
    with TestClient(app) as browser:
        first=browser.post('/v3/login',data={'associate_id':'1001','mode':'start','shift_type':'S2','shift_date':'2026-10-20'},follow_redirects=False)
        assert first.status_code==303
        sid1=db.get_active_shifts()[0]['id']
        browser.get('/v3/logout')
        # Isaac's personal 2759 is NOT flagged is_manager in the roster --
        # only the master-credential check elevates it for this session.
        master=browser.post('/v3/login',data={'associate_id':'2759','mode':'start','shift_type':'S2','shift_date':'2026-10-20'})
        assert "getElementById('master-shift-dialog').showModal();" in master.text
        picked=browser.post('/v3/master/select',data={'shift_type':'S2'},follow_redirects=False)
        assert picked.status_code==303
        assert f'shift_id={sid1}' in picked.headers['location']
        assert browser.get(f'/v3/operations?shift_id={sid1}').status_code==200
        # Attribution stays Isaac's own number, not the synthetic 8972 identity.
        assert db.get_shift(sid1)['opened_by_assoc']=='1001'

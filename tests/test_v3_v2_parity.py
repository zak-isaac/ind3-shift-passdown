"""V3 keeps V2 features working while changing only the Cetaris work-order import."""
from datetime import datetime, date, time, timedelta
from pathlib import Path
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
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
    visited=[]
    async def old_dashboard(request:Request,shift_id:int):
        visited.append(shift_id)
        return HTMLResponse(f'V2 LOTO OOS/RTS Vendors Report Send End Shift {shift_id}')
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
       legacy_lead_page=old_dashboard,
    ))
    with TestClient(app) as browser:
        entry=browser.get('/v3/login')
        assert entry.status_code==200
        assert 'walmart-logo-white.svg' in entry.text
        assert 'Your associate number' in entry.text
        assert 'name="pin"' not in entry.text
        deny=browser.post('/v3/login',data={'associate_id':'8972','mode':'start','shift_type':'S2','shift_date':'2026-10-20'})
        assert 'Associate number not found' in deny.text
        first=browser.post('/v3/login',data={'associate_id':'1001','mode':'start','shift_type':'S2','shift_date':'2026-10-20'},follow_redirects=False)
        assert first.status_code==303
        sid1=db.get_active_shifts()[0]['id']
        assert db.get_shift(sid1)['opened_by_role']=='mst'
        assert db.get_shift(sid1)['opened_by_assoc']=='1001'
        assert browser.get(f'/v3/operations?shift_id={sid1}').status_code==200
        assert visited==[sid1]
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
        duplicate=browser.post('/v3/login',data={'associate_id':'2002','mode':'start','shift_type':'S3','shift_date':'2026-10-20'})
        assert 'already active' in duplicate.text
        # Restore the original session and verify that it still sees only sid1.
        browser.get('/v3/logout')
        own=browser.post('/v3/login',data={'associate_id':'1001','mode':'resume','shift_id':sid1},follow_redirects=False)
        assert own.status_code==303
        assert browser.get(f'/v3/operations?shift_id={sid1}').status_code==200
        assert browser.get(f'/v3/operations?shift_id={sid2}').status_code==403

"""Scope V3 MST identities to one active shift and verify the XLSX modal workflow."""
from pathlib import Path
from datetime import datetime, time, timedelta
import re
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.testclient import TestClient
import db
from v3_routes import create_v3_router

ROOT=Path(__file__).resolve().parents[1]

def test_own_shift_only_and_xlsx_technician_dialog(tmp_path):
    db.set_db_path(tmp_path/'v3identity.db');db.init_db()
    s_a=db.start_shift('2026-10-20','S1','MST Alpha',opened_by_assoc='a100')
    s_b=db.start_shift('2026-10-20','S2','MST Beta',opened_by_assoc='b200')
    roster={'a100':{'is_mst':True,'display_name':'MST Alpha'},'b200':{'is_mst':True,'display_name':'MST Beta'}}
    app=FastAPI()
    app.mount('/static',StaticFiles(directory=str(ROOT/'static')),name='static')
    app.include_router(create_v3_router(
        templates=Jinja2Templates(directory=str(ROOT/'templates')),
        is_lead_authed=lambda req: False,
        can_manage_shift=lambda req,sid: False,
        resolve_my_shift=lambda req: None,
        shift_window=lambda st,d:(datetime.combine(d,time(0)),datetime.combine(d+timedelta(days=1),time(0))),
        lead_redirect=lambda dest: RedirectResponse('/lead/login',status_code=303),
        get_mst_roster=lambda:roster,
        verify_mst_pin=lambda pin:pin=='teamtest',
        start_eligibility=lambda a,shift,now:(True,False,''),
        allowed_shift_days={'S1':{1},'S2':{1}},
        now_func=lambda:datetime(2026,10,20,13,0),
    ))
    with TestClient(app) as client:
        assert client.get('/v3',follow_redirects=False).headers['location']=='/v3/login'
        assert 'Associate number not found' in client.post('/v3/login',data={'associate_id':'not-a-tech','mode':'signin'}).text
        sign=client.post('/v3/login',data={'associate_id':'a100','mode':'signin'},follow_redirects=False)
        assert sign.status_code==303 and f'shift_id={s_a}' in sign.headers['location']
        assert client.get('/v3',params={'shift_id':s_a}).status_code==200
        assert client.get('/v3',params={'shift_id':s_b},follow_redirects=False).status_code==303
        assert client.post('/v3/manual',data={'shift_id':s_b,'unit':'M1','issue':'x'}).status_code==403
        assert client.post(f'/v3/attach/{s_b}',follow_redirects=False).status_code==403
        assert client.get('/v3/choose').text.count('Resume shift')==1
        sample=Path('/mnt/data/trial.xlsx')
        if sample.exists():
            resp=client.post('/v3/import',data={'shift_id':s_a},files={'file':('trial.xlsx',sample.read_bytes())})
            assert resp.status_code==200
            assert 'data-staged="true"' in resp.text
            assert 'Who worked this shift?' in resp.text
            assert 'Ssaku, Isaac' in resp.text
            token=re.search(r'/v3/import/([a-f0-9]{32})/confirm',resp.text).group(1)
            confirmation=client.post(f'/v3/import/{token}/confirm',data={'shift_id':s_a,'techs':['Ssaku, Isaac']},follow_redirects=False)
            assert confirmation.status_code==303
            resp2=client.get('/v3/technicians',params={'shift_id':s_a})
            assert resp2.status_code==200 and 'data-staged="true"' in resp2.text
            assert 'checked' in resp2.text
        client.get('/v3/logout')
        assert client.get('/v3',follow_redirects=False).headers['location']=='/v3/login'
        second=client.post('/v3/login',data={'associate_id':'b200','mode':'signin'},follow_redirects=False)
        assert f'shift_id={s_b}' in second.headers['location']
        assert client.get('/v3',params={'shift_id':s_a},follow_redirects=False).status_code==303

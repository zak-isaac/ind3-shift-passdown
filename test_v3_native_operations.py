"""Native V3 maintenance controls, shift boundaries and V2 report integration."""
from datetime import datetime, timedelta, time
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates
import db
from v3_routes import create_v3_router

ROOT=Path(__file__).resolve().parents[1]

def test_native_oos_rts_vendors_carryovers_and_shift_isolation(tmp_path):
    db.set_db_path(tmp_path/'native.db');db.init_db()
    prior=db.start_shift('2026-10-19','S1','Earlier owner',opened_by_assoc='9000')
    old_entry=db.add_entry(prior,'Tech','PUMP0001','Unresolved','Investigated','OOS')
    db.set_carry_over(old_entry,True)
    db.close_shift(prior)
    sid=db.start_shift('2026-10-20','S2','Current owner',opened_by_assoc='1001')
    other=db.start_shift('2026-10-20','S3','Other owner',opened_by_assoc='2002')
    roster={'1001':{'display_name':'Current owner','is_mst':True,'is_manager':False},
            '2002':{'display_name':'Other owner','is_mst':True,'is_manager':False}}
    app=FastAPI()
    app.include_router(create_v3_router(
        Jinja2Templates(directory=str(ROOT/'templates')),
        lambda request:False,lambda request,shift:False,lambda request:None,
        lambda shift,d:(datetime.combine(d,time(0)),datetime.combine(d+timedelta(days=1),time(0))),
        lambda url:None,get_mst_roster=lambda:roster))
    with TestClient(app) as c:
        assert c.post('/v3/login',data={'associate_id':'1001','mode':'resume','shift_id':sid},follow_redirects=False).status_code==303
        page=c.get(f'/v3?shift_id={sid}');assert page.status_code==200
        assert 'PUMP0001' in page.text
        assert c.post('/v3/oos/add',data={'shift_id':other,'unit':'ASSET01'}).status_code==403
        assert c.post('/v3/oos/add',data={'shift_id':sid,'unit':'ASSET01'},follow_redirects=False).status_code==303
        assert 'ASSET01' in db.get_oos_units()
        assert c.post('/v3/oos/rts',data={'shift_id':sid,'unit':'ASSET01'},follow_redirects=False).status_code==303
        assert 'ASSET01' not in db.get_oos_units()
        assert db.get_rts_events(sid)==['ASSET01']
        assert c.post('/v3/oos/rts',data={'shift_id':sid,'unit':'ASSET01'}).status_code==409
        assert c.post('/v3/vendor/add',data={'shift_id':sid,'name':'Edwards','notes':'Restroom repair'},follow_redirects=False).status_code==303
        page=c.get(f'/v3?shift_id={sid}');assert 'Edwards' in page.text
        assert c.post(f'/v3/carry-over/{old_entry}/adopt',data={'shift_id':sid},follow_redirects=False).status_code==303
        assert any(e['unit']=='PUMP0001' for e in db.get_entries(sid))
        assert not db.get_entry(old_entry)['carry_over']
        assert c.post(f'/v3/carry-over/{old_entry}/adopt',data={'shift_id':sid}).status_code==404
        assert c.post('/v3/rts/undo',data={'shift_id':sid,'unit':'ASSET01'},follow_redirects=False).status_code==303
        assert not db.get_rts_events(sid)

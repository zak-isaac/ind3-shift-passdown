"""V3-only routing and report authorization regression tests."""
from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient
import db
import v3_identity
from v3_only import enable_v3_only

def make_app(monkeypatch):
    app=FastAPI()
    @app.get("/")
    def home(): return PlainTextResponse("old home")
    @app.get("/v2-home")
    def old_home(): return PlainTextResponse("V2")
    @app.post("/lead/generate")
    def old_generate(): return PlainTextResponse("legacy mutation")
    @app.get("/tech/login")
    def old_tech(): return PlainTextResponse("legacy tech")
    @app.get("/v3/login")
    def login(): return PlainTextResponse("V3 login")
    @app.get("/v3")
    def workspace(): return PlainTextResponse("V3 workspace")
    @app.get("/report/{report_id}/pdf")
    def pdf(report_id:int): return PlainTextResponse(f"pdf {report_id}")
    @app.get("/report/{report_id}/compose-outlook")
    def outlook(report_id:int): return PlainTextResponse(f"outlook {report_id}")
    enable_v3_only(app,roster_provider=lambda:{})
    return app

def test_only_v3_public_routes_and_no_v2_actions(monkeypatch):
    app=make_app(monkeypatch)
    routes={getattr(route,"path","") for route in app.router.routes}
    assert "/v2-home" not in routes
    assert "/lead/generate" not in routes
    assert "/tech/login" not in routes
    assert "/v3/login" in routes
    assert "/report/{report_id}/pdf" in routes
    with TestClient(app) as client:
        response=client.get("/",follow_redirects=False)
        assert response.status_code==303 and response.headers["location"]=="/v3"
        assert client.get("/v3/login").text=="V3 login"
        assert client.get("/v3").text=="V3 workspace"
        assert client.get("/v2-home",follow_redirects=False).status_code==303
        assert client.post("/lead/generate").status_code==410
        assert client.get("/report/12/pdf").status_code==401

def test_export_requires_owner_or_verified_master(monkeypatch):
    app=make_app(monkeypatch)
    monkeypatch.setattr(db,"get_report",lambda report_id:{"id":report_id,"shift_id":7} if report_id==12 else None)
    monkeypatch.setattr(db,"get_shift",lambda shift_id:{"id":7,"opened_by_assoc":"123","is_active":0} if shift_id==7 else None)
    session={"associate":"999","shift_id":7,"profile":{"is_manager":False}}
    monkeypatch.setattr(v3_identity,"read",lambda request,roster:session)
    with TestClient(app) as client:
        assert client.get("/report/12/pdf").status_code==403
        session["associate"]="123"
        assert client.get("/report/12/pdf").status_code==200
        assert client.get("/report/12/compose-outlook").status_code==200
        assert client.get("/report/13/pdf").status_code==404
        session["associate"]="999"
        session["profile"]={"is_manager":True}
        assert client.get("/report/12/pdf").status_code==200

def test_actual_main_registers_only_v3_and_exports():
    import main
    paths={getattr(route,"path","") for route in main.app.router.routes}
    print("MAIN V3-ONLY ROUTES:", sorted(paths))
    assert "/" in paths and "/v3" in paths and "/v3/login" in paths, sorted(paths)
    assert "/v2-home" not in paths
    assert "/lead" not in paths
    assert "/tech" not in paths
    assert "/lead/generate" not in paths
    assert "/report/{report_id}/compose-outlook" in paths

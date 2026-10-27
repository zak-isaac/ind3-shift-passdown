"""Show all ignored Cetaris WOs without counting them as shift work."""
from pathlib import Path
from jinja2 import Environment
from v3_import_audit import preview_status, build_audit
ROOT=Path(__file__).resolve().parents[1]

def rec(wo,tech,status,start="2026-10-25T08:30:00"):
    return {"wo_number":wo,"tech_name":tech,"status":status,"start_at":start,
            "asset_id":"M1","closed_at":start,"rfr":"Proactive Repair"}

def test_audit_preserves_other_shift_old_and_in_process():
    rows=[
        rec("101","Carr, Alexander","Closed"),
        rec("101","Spires, Bradley","Closed"),
        rec("102","Carr, Alexander","Closed","2026-09-01T09:00:00"),
        rec("103","Rob Tice","Closed"),
        rec("104","Carr, Alexander","In Process"),
        rec("105","Carr, Alexander","Completed"),
        rec("105","Carr, Alexander","Completed")]
    audit=build_audit(rows,{"Carr, Alexander","Spires, Bradley"},
                      [rows[0],rows[1],rows[5],rows[6]],[{**rows[0],"kind":"happening"}])
    assert audit["work_orders"]==5
    assert audit["included_work_orders"]==1
    assert audit["ignored_count"]==4
    reasons={r["wo_number"]:r["reason"] for r in audit["ignored"]}
    assert "Outside this shift" in reasons["102"]
    assert "not selected" in reasons["103"]
    assert "Not Closed" in reasons["104"]
    assert "Completed but not Closed" in reasons["105"]
    assert len([r for r in audit["ignored"] if r["wo_number"]=="105"])==1

def test_file_preview_status_is_unique_by_wo():
    rows=[rec("111","Carr, Alexander","Closed"),rec("111","Spires, Bradley","Closed")]
    assert preview_status(rows)=={"work_orders":1,"closed":1,"completed":0,"other":0,"technicians":2}

def test_workspace_jinja_keeps_production_shell():
    html=(ROOT/"templates/v3/workspace.html").read_text(encoding="utf-8")
    assert 'class="shell design2-shell"' in html
    # 2026-10-26: restored the detailed read-only "Imported WO audit" drawer
    # (ignored WOs + reasons) at Isaac's explicit request -- the one-line
    # "Cetaris import summary" and the assertion hiding the ignored-count
    # drawer were both from an earlier same-night simplification that he's
    # now reversing. The interactive Needs Review Include/Dismiss panel
    # added earlier the same day stays removed; this is read-only again.
    assert "Imported WO audit" in html
    assert "Show {{ import_audit.ignored_count }}" in html
    Environment().parse(html)

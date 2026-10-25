"""Real bullets and matching text across V3 report renderers."""
from io import BytesIO
from pathlib import Path
from docx import Document
from jinja2 import Environment, FileSystemLoader
from exporters import export_docx, export_pdf
from formatter import format_email, format_email_passdown, format_email_passdown_html, format_html_preview
from report_models import ReportData
from v3_report_layout import build_shift_happenings, heading, remark

ROOT = Path(__file__).resolve().parents[1]

def example():
    rows = [
        {"id":1,"unit":"VOIDFILL0501","wo_number":"28010011","source":"cmms",
         "issue":"WO #28010011 — Side guide not gripping boxes",
         "raw_remarks":'["checked colum tention; PLC RTS"]',"excluded":0},
        {"id":2,"unit":"VOIDFILL0501","wo_number":"28010011","source":"cmms",
         "issue":"WO #28010011 — Side guide not gripping boxes",
         "raw_remarks":'["Recalibrated side guide and verified operation. RTS"]',"excluded":0},
        {"id":3,"unit":"BAG0007","wo_number":"28010012","source":"cmms",
         "issue":"WO #28010012 — Controller failure",
         "raw_remarks":'["Replaced FSM controller on BAG0007"]',"excluded":0},
        {"id":4,"unit":"HIDDEN0001","wo_number":"28010013","source":"cmms",
         "issue":"WO #28010013 — Excluded issue","raw_remarks":'["Do not include"]',"excluded":1},
    ]
    happenings=build_shift_happenings(rows,{1:{"cause_text":"Side guide not gripping boxes"}})
    return happenings, ReportData(date="2026-10-24",shift_type="S2",lead_name="Test MST",happenings=happenings,pm_count=3)

def test_multi_tech_duplicates_not_repeated_but_unique_details_survive():
    rows=[
        {"id":101,"unit":"FLEX0009","wo_number":"900101","source":"cmms",
         "issue":"WO #900101 — Motor brush maintenance",
         "raw_remarks":'["Motor brush cleanup"]',"excluded":0},
        {"id":102,"unit":"FLEX0009","wo_number":"900101","source":"cmms",
         "issue":"WO #900101 — Motor brush maintenance",
         "raw_remarks":'["motor brush cleanup."]',"excluded":0},
        {"id":103,"unit":"FLEX0009","wo_number":"900101","source":"cmms",
         "issue":"WO #900101 — Motor brush maintenance",
         "raw_remarks":'["Inspected both motor brushes"]',"excluded":0},
    ]
    h=build_shift_happenings(rows)
    assert len(h)==1
    assert heading(h[0])=="FLEX0009: Motor brush maintenance"
    assert remark(h[0])=="Motor brush cleanup; Inspected both motor brushes"
    assert "replaced" not in remark(h[0]).casefold()


def test_distinct_measurements_and_status_are_never_fuzzy_deduped():
    rows=[
        {"id":201,"unit":"M1234","wo_number":"900202","source":"cmms",
         "issue":"WO #900202 — Brake troubleshooting",
         "raw_remarks":'["Braking distance 6ft; OOS"]',"excluded":0},
        {"id":202,"unit":"M1234","wo_number":"900202","source":"cmms",
         "issue":"WO #900202 — Brake troubleshooting",
         "raw_remarks":'["Plugging distance 10ft; RTS"]',"excluded":0},
    ]
    h=build_shift_happenings(rows)
    assert len(h)==1
    assert "6ft" in remark(h[0]) and "10ft" in remark(h[0])
    assert "OOS" in remark(h[0]) and "RTS" in remark(h[0])
    assert remark(h[0]).count("; ")>=1


def test_different_components_or_directions_are_never_lost():
    rows=[
        {"id":301,"unit":"VOIDFILL1101","wo_number":"900303","source":"cmms",
         "issue":"WO #900303 — Rail damage",
         "raw_remarks":'["Replaced left rail"]',"excluded":0},
        {"id":302,"unit":"VOIDFILL1101","wo_number":"900303","source":"cmms",
         "issue":"WO #900303 — Rail damage",
         "raw_remarks":'["Assisted with right rail"]',"excluded":0},
    ]
    h=build_shift_happenings(rows)
    assert len(h)==1
    assert "Replaced left rail" in remark(h[0])
    assert "Assisted with right rail" in remark(h[0])

def test_one_heading_one_bullet_per_work_order():
    h,data=example()
    assert len(h)==2
    assert heading(h[0])=="VOIDFILL0501: Side guide not gripping boxes"
    assert remark(h[0])=="Checked column tension; PLC RTS; Recalibrated side guide and verified operation. RTS"
    assert "HIDDEN0001" not in repr(data)

def test_mst_edited_cause_overrides_original_technician_row():
    rows=[
        {"id":8,"unit":"VOIDFILL0201","wo_number":"28010055",
         "source":"cmms","issue":"WO #28010055 — Old cause",
         "raw_remarks":'["Inspected guide"]',"excluded":0},
        {"id":9,"unit":"VOIDFILL0201","wo_number":"28010055",
         "source":"cmms","issue":"WO #28010055 — Old cause",
         "raw_remarks":'["Calibrated guide"]',"excluded":0},
    ]
    meta={
        8: {"cause_text":"Old cause", "mst_edited":0},
        9: {"cause_text":"Side guide slipping", "mst_edited":1},
    }
    report=build_shift_happenings(rows,meta)
    assert len(report)==1
    assert heading(report[0])=="VOIDFILL0201: Side guide slipping"
    assert remark(report[0])=="Inspected guide; Calibrated guide"


def test_manual_and_missing_remarks_preserved():
    records=[
        {"id":5,"unit":"SCRUB0004","wo_number":"","source":"","issue":"Brushes worn",
         "action":"replaced two brushes. RTS","excluded":0},
        {"id":6,"unit":"BAG0009","wo_number":"28010020","source":"cmms",
         "issue":"WO #28010020 — Motor will not start","raw_remarks":"[]","excluded":0}]
    h=build_shift_happenings(records)
    assert heading(h[0])=="SCRUB0004: Brushes worn"
    assert remark(h[0])=="Replaced two brushes. RTS"
    assert remark(h[1])=="No technician remarks recorded."

def test_text_preview_and_outlook_match():
    h,data=example()
    heading_text=heading(h[0]); remark_text=remark(h[0])
    all_outputs=[format_email(data),format_email_passdown(data),format_email_passdown_html(data),format_html_preview(data)]
    for content in all_outputs:
        assert heading_text in content and remark_text in content
        assert "HIDDEN0001" not in content
    assert "    •  "+remark_text in all_outputs[0]
    assert "  • "+remark_text in all_outputs[1]
    assert "<li " in all_outputs[2] and "<li " in all_outputs[3]
    restored=ReportData.model_validate_json(data.model_dump_json())
    assert restored.happenings[0].cause==h[0].cause

def test_v3_print_preview_uses_indented_bullet():
    h,data=example()
    env=Environment(loader=FileSystemLoader(str(ROOT/"templates")))
    html=env.get_template("v3/preview.html").render(
        shift={"date":"2026-10-24","shift_type":"S2","lead_name":"Test MST","id":1},
        report_happenings=h,pm_count=3,loto={"loto_verified":0,"loto_missing_keys":""},
        oos_units=[],rts_units=[],vendors=[],carry_overs=[],shift_notes="")
    assert heading(h[0]) in html
    assert "<li>"+remark(h[0])+"</li>" in html
    assert "Completed or Closed PM work orders" in html

def test_word_and_pdf_exporters_contain_bullet():
    h,data=example()
    word=Document(BytesIO(export_docx(data)))
    paragraphs=[p.text for p in word.paragraphs]
    assert heading(h[0]) in paragraphs
    assert "•  "+remark(h[0]) in paragraphs
    assert export_pdf(data).startswith(b"%PDF-")

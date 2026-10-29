"""V3 Report Studio preserves shift workflows while keeping the approved minimal chrome."""
from pathlib import Path
from jinja2 import Environment

ROOT=Path(__file__).resolve().parents[1]

def test_production_v3_shell_and_import_audit():
    content=(ROOT/"templates/v3/workspace.html").read_text(encoding="utf-8")
    for token in (
        'class="shell design2-shell" id="rs-shell"',
        "{% include 'v3/_sidebar.html' %}",
        "{% include 'v3/_topbar.html' %}",
        "Imported WO audit",
        'id="rs-audit-panel"',
        'data-ai-remark-wo',
        'id="v3-tech-dialog"',
        'id="v3-import-dialog"',
        'name="pm_file"',
        'name="missing_keys"',
        'id="rs-pagination"',
        '/v3/happening/{{ g.main_id }}/exclude',
        '/v3/operations?shift_id={{ shift.id }}',
    ):
        assert token in content
    assert "is_dev_mode" not in content
    assert 'd2-import-flow card' not in content
    assert 'd2-kpis' not in content
    Environment().parse(content)

def test_ordinary_login_without_developer_mode():
    routes=(ROOT/"v3_routes.py").read_text(encoding="utf-8")
    main=(ROOT/"main.py").read_text(encoding="utf-8")
    assert 'T8972' not in routes
    assert '/dev/workspace' not in routes
    assert '/dev/login' not in main
    assert 'dev_mode_sandbox' not in main
    assert 'IND3_OLLAMA_MODEL' in (ROOT/"v3_ollama_background.py").read_text(encoding="utf-8")

def test_approved_sidebar_does_not_repeat_header_details():
    sidebar=(ROOT/"templates/v3/_sidebar.html").read_text(encoding="utf-8")
    topbar=(ROOT/"templates/v3/_topbar.html").read_text(encoding="utf-8")
    css=(ROOT/"static/v3_design2.css").read_text(encoding="utf-8")
    assert 'id="v3-sidebar"' in sidebar
    for route in ('/v3?shift_id={{ sid }}','/v3/history?shift_id={{ sid }}',
                  '/v3/analytics?shift_id={{ sid }}','/v3/admin','/v3/logout'):
        assert route in sidebar
    for title in ('Shift Report','Shift History','Analytics','Administration','Sign Out'):
        assert title in sidebar
    for redundant in ('Dashboard','Work Orders','PMs','Change Technicians',
                      'data-v3-live-time','data-v3-live-date'):
        assert redundant not in sidebar
    assert 'rs-header-logo' in topbar
    assert 'rs-shift-badge' in topbar
    assert 'rs-shift-hours' in topbar
    assert 'shift.date' not in sidebar
    assert "d2-sidebar-profile" in sidebar
    assert "rs-sidebar" in css
    assert "TEST MODE" not in sidebar
    Environment().parse(sidebar)
    Environment().parse(topbar)

def test_work_order_tools_and_report_sections_are_real_routes():
    content=(ROOT/"templates/v3/workspace.html").read_text(encoding="utf-8")
    for route in ('/v3/import','/v3/pm-upload','/v3/loto','/v3/notes',
                  '/v3/vendor/add','/v3/oos/add','/v3/oos/rts','/v3/rts/undo'):
        assert route in content
    assert 'v3OpenHappening({{ g.main_id }},{{ shift.id }},true)' in content
    assert 'data-rs-missing-key' in content
    assert 'range(1,21)' in content
    js=(ROOT/"static/v3.js").read_text(encoding="utf-8")
    assert 'function d2ChangePage(delta)' in js
    assert 'function v3SyncMissingKeys()' in js

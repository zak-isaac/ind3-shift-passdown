"""Production-only routing regression.
Developer routes, cookie database switching and hard-coded test technician maps
must not be present; operational pipelines remain.
"""
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def test_production_only_paths():
    main=(ROOT/"main.py").read_text()
    routes=(ROOT/"v3_routes.py").read_text()
    html=(ROOT/"templates/v3/workspace.html").read_text()
    assert "/dev/login" not in main
    assert "db.use_sandbox_db" not in main
    assert "/dev/shift" not in routes
    assert "T8972" not in routes
    assert "_TEST_TECH_SHIFTS" not in routes
    assert "is_dev_mode" not in html
    assert "def confirm_import(" in routes
    assert "build_audit(" in routes
    assert "ollama_drafts.queue_shift(shift_id)" in routes
    assert "approved_for_report" in main

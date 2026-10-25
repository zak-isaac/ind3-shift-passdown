"""Production V3 workspace must retain the approved command-center shell."""
from pathlib import Path
from jinja2 import Environment

ROOT=Path(__file__).resolve().parents[1]
def test_production_v3_shell_and_import_audit():
    content=(ROOT/"templates/v3/workspace.html").read_text()
    for token in ('class="shell design2-shell"',
                  "{% include 'v3/_sidebar.html' %}",
                  "{% include 'v3/_topbar.html' %}",
                  "Imported WO audit"):
        assert token in content
    assert "is_dev_mode" not in content
    Environment().parse(content)
def test_ordinary_login_without_developer_mode():
    routes=(ROOT/"v3_routes.py").read_text()
    main=(ROOT/"main.py").read_text()
    assert 'T8972' not in routes
    assert '/dev/workspace' not in routes
    assert '/dev/login' not in main
    assert 'dev_mode_sandbox' not in main
    assert 'IND3_OLLAMA_MODEL' in (ROOT/"v3_ollama_background.py").read_text()

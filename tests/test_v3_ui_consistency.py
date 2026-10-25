"""The test workspace must retain the production V3 shell."""
from pathlib import Path
from jinja2 import Environment

ROOT=Path(__file__).resolve().parents[1]
def test_production_layout_is_preserved_and_templates_parse():
    for name in ("workspace.html","dev_workspace.html"):
        content=(ROOT/"templates"/"v3"/name).read_text()
        assert 'class="shell design2-shell"' in content
        assert "{% include 'v3/_sidebar.html' %}" in content
        assert "{% include 'v3/_topbar.html' %}" in content
        Environment().parse(content)
    current=(ROOT/"templates"/"v3"/"workspace.html").read_text()
    assert "Frozen for simulation" not in current
    test=(ROOT/"templates"/"v3"/"dev_workspace.html").read_text()
    assert "name=\"simulated_at\"" not in test
    assert "data-v3-live-clock" in test

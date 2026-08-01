"""
End-to-end pipeline test: parse → AI rewrite → report format

Tests the full chain with real maintenance remarks that a tech would type.
Runs against the LIVE Ollama + phi4-mini instance on localhost:11434.

Usage:
    .venv\Scripts\python.exe tests\test_pipeline_e2e.py
"""
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import ai_rewriter
from rewriter import rewrite_happening, rewrite_all
from report_models import HappeningInput, RewrittenHappening
from formatter import format_email_passdown_html

# ── Colour helpers ─────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

def ok(msg):   print(f"  {GREEN}PASS{RESET}  {msg}")
def fail(msg): print(f"  {RED}FAIL{RESET}  {msg}"); sys.exit(1)
def info(msg): print(f"  {YELLOW}INFO{RESET}  {msg}")

# ── Sample remarks (realistic tech input) ──────────────────────────────────────
SAMPLES = [
    HappeningInput(title="RC0042", raw_note="horn not working replaced fuse rts"),
    HappeningInput(title="T8 Conveyor", raw_note="belt slipping adjusted tension checked alignment all good"),
    HappeningInput(title="Dock Door 14", raw_note="door not closing all the way spring broke waiting on parts"),
    HappeningInput(title="PA System Zone 3", raw_note="intermittent static. swapped speaker. tested ok"),
    HappeningInput(title="Charger Bay 7", raw_note="charger throwing fault code E04 reset breaker unit back online"),
]

def check_parse(item: HappeningInput) -> bool:
    """Verify the raw note has meaningful content before sending to AI."""
    if not item.title or not item.title.strip():
        return False
    if not item.raw_note or not item.raw_note.strip():
        return False
    if len(item.raw_note.strip()) < 5:
        return False
    return True

def preserves_key_terms(original: str, rewritten: str) -> list[str]:
    """Check that asset/equipment names from original appear in rewritten."""
    # Extract potential asset identifiers (alphanumeric tokens 2+ chars)
    import re
    tokens = re.findall(r'\b[A-Z0-9][A-Z0-9]{1,}\b', original.upper())
    missing = [t for t in tokens if t not in rewritten.upper()]
    return missing

# ── Run tests ─────────────────────────────────────────────────────────────────
print(f"\n{BOLD}=== Passdown Pipeline E2E Test ==={RESET}")
print(f"  Testing: parse -> AI rewrite -> report\n")

# Step 1: Check Ollama is up
print(f"{BOLD}Step 1: Ollama + phi4-mini health check{RESET}")
ready = ai_rewriter.check_ollama()
if not ready:
    fail("Ollama or phi4-mini not available — cannot run E2E test")
ok("Ollama running, phi4-mini available")

# Step 2: Parse validation
print(f"\n{BOLD}Step 2: Parse validation (all {len(SAMPLES)} samples){RESET}")
for item in SAMPLES:
    if not check_parse(item):
        fail(f"Parse failed for: {item.title!r} / {item.raw_note!r}")
    ok(f"{item.title} — parse OK ({len(item.raw_note)} chars)")

# Step 3: AI rewrite — one by one with timing
print(f"\n{BOLD}Step 3: AI rewrite (live phi4-mini){RESET}")
rewritten_items: list[RewrittenHappening] = []

for item in SAMPLES:
    t0 = time.perf_counter()
    result = rewrite_happening(item)
    elapsed = time.perf_counter() - t0

    rewritten_items.append(result)

    # Check: got a result
    if not result.paragraph or not result.paragraph.strip():
        fail(f"{item.title} — empty paragraph returned")

    # Check: AI actually changed something (not just echoed input)
    raw_lower    = item.raw_note.lower().strip()
    result_lower = result.paragraph.lower().strip()
    ai_improved  = raw_lower != result_lower

    # Check: key technical terms preserved
    missing = preserves_key_terms(item.title, result.paragraph)

    status = f"{elapsed:.1f}s | {'AI' if ai_improved else 'template fallback'}"
    if missing:
        info(f"{item.title} — {status} | missing terms: {missing}")
    else:
        ok(f"{item.title} — {status}")

    print(f"    RAW: {item.raw_note}")
    print(f"    OUT: {result.paragraph}\n")

# Step 4: Report format check
print(f"{BOLD}Step 4: Report formatting{RESET}")
from report_models import ReportData
from datetime import date

data = ReportData(
    date=date.today().isoformat(),
    shift_type="Days",
    lead_name="Test Lead",
    dc_number="8972",
    happenings=rewritten_items,
    vendors=["Acme Parts Co"],
    ro=["Completed monthly PM on dock doors", "Updated LOTO log"],
    pm_count=3,
)

html = format_email_passdown_html(data)

if not html or len(html) < 500:
    fail("HTML output too short — formatter likely crashed")
if not any(r.title in html for r in rewritten_items):
    fail("No happening titles found in rendered HTML")
if "Days End of Shift Passdown" not in html:
    fail("Shift type header missing from HTML")
if "Reliable Operations" not in html:
    fail("RO section missing from HTML")

ok(f"HTML report generated ({len(html):,} chars)")

print(f"\n{BOLD}{GREEN}All steps passed! Pipeline is working end-to-end.{RESET}")
print(f"  parse -> AI rewrite -> formatter -> OK\n")

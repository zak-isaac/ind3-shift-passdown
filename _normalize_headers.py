"""
Normalize every page header to match lead.html:
  - sticky top-0, z-index:9999, shadow-md
  - inner div: min-height:56px, no py-N padding, px-5
"""
import re

PAGES = [
    "templates/tech.html",
    "templates/admin.html",
    "templates/history.html",
    "templates/asset_history.html",
    "templates/share.html",
    "templates/shift_ended.html",
    "templates/shift_report_view.html",
    "templates/weekly.html",
    "templates/home.html",
]

# Normalise outer <header> class
HEADER_RE = re.compile(
    r'<header class="bg-wm-blue[^"]*">'
)
HEADER_GOOD = '<header class="bg-wm-blue sticky top-0 shadow-md" style="z-index:9999;">'

# Normalise inner container: strip py-N, ensure min-height:56px via style
INNER_RE = re.compile(
    r'<div class="([^"]*?)\bflex\b([^"]*?)items-center([^"]*?)"(\s*style="[^"]*")?>'
)

for path in PAGES:
    txt = open(path, encoding="utf-8").read()
    orig = txt

    # 1. Fix outer header
    txt = HEADER_RE.sub(HEADER_GOOD, txt, count=1)

    # 2. Fix the FIRST flex items-center div inside header (the inner container)
    #    Remove py-N classes, keep px-N, ensure style min-height:56px
    def fix_inner(m):
        before = m.group(1)
        between = m.group(2)
        after = m.group(3)
        classes = (before + " flex" + between + "items-center" + after)
        # strip any py-* tokens
        classes = re.sub(r'\bpy-\d+(\.\d+)?\b', '', classes)
        # strip extra spaces
        classes = re.sub(r'  +', ' ', classes).strip()
        return f'<div class="{classes}" style="min-height:56px;">'

    # Apply only once (first match = the header inner div)
    txt = INNER_RE.sub(fix_inner, txt, count=1)

    if txt != orig:
        open(path, "w", encoding="utf-8").write(txt)
        print(f"updated: {path}")
    else:
        print(f"no change: {path}")

print("Done.")

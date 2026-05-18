"""
Fix: 224px logo inside flex items-center pushes the column to 224px tall,
which overflow:hidden centers at -76px from header top, hiding the hamburger.

Fix: Wrap every logo img in a height-constrained div (56px).
Flex layout now sees a 56px item. The 224px SVG is clipped by ITS container,
not the entire header. Remove overflow:hidden from header + inner div (no longer needed).
"""
import glob, re

PAGES = glob.glob("templates/*.html") + glob.glob("templates/partials/*.html")

# The logo img pattern
LOGO_RE = re.compile(
    r'(<img\s[^>]*walmart-logo-white\.svg[^>]*style="height:224px;width:auto;"[^>]*/>)',
    re.DOTALL
)
WRAPPER = (
    '<div style="height:56px;overflow:hidden;display:flex;align-items:center;flex-shrink:0;">'
    r'\1'
    '</div>'
)

# Remove overflow:hidden from header-level elements (no longer needed now logo has its own clip)
HEADER_OVERFLOW_RE   = re.compile(r'(style="z-index:9999;)\s*height:72px;\s*overflow:hidden;(")')
INNER_DIV_OVERFLOW_RE = re.compile(r'(style=")\s*height:72px;\s*overflow:hidden;(")')

for path in PAGES:
    txt = open(path, encoding="utf-8").read()
    orig = txt

    # 1. Wrap logo img in height-locked container (only if not already wrapped)
    if 'height:224px' in txt and 'height:56px;overflow:hidden' not in txt:
        txt = LOGO_RE.sub(WRAPPER, txt)

    # 2. Strip overflow:hidden from header outer + inner div — logo wrapper handles it now
    txt = HEADER_OVERFLOW_RE.sub(r'\1 height:72px;\2', txt)
    txt = INNER_DIV_OVERFLOW_RE.sub(r'\1height:72px;\2', txt)

    if txt != orig:
        open(path, "w", encoding="utf-8").write(txt)
        print(f"updated: {path}")

print("Done.")

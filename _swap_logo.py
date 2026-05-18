"""Replace wrench.svg + WALMART text with Walmart logo SVG across all templates."""
import os, re

TEMPLATES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
LOGO_TAG  = '<img src="/static/walmart-logo.svg" alt="Walmart" style="height:36px;width:auto;" />'

# Patterns to replace
WRENCH_RE = re.compile(r'<img\s+src="/static/wrench\.svg"[^>]*/>')
WALMART_SPAN_RE = re.compile(
    r'<span\s+class="[^"]*font-black[^"]*text-lg[^"]*"\s*>WALMART</span>'
)
WALMART_SPAN2_RE = re.compile(
    r'<span\s+class="font-black text-lg"\s*>WALMART</span>'
)

changed = []
for fname in os.listdir(TEMPLATES):
    if not fname.endswith('.html'):
        continue
    path = os.path.join(TEMPLATES, fname)
    orig = open(path, encoding='utf-8').read()
    txt  = orig

    # Only touch files that have the wrench
    if 'wrench.svg' not in txt:
        continue

    # Skip the JS icon reference in lead.html
    txt = WRENCH_RE.sub(LOGO_TAG, txt)
    txt = WALMART_SPAN_RE.sub('', txt)
    txt = WALMART_SPAN2_RE.sub('', txt)

    if txt != orig:
        open(path, 'w', encoding='utf-8').write(txt)
        changed.append(fname)
        print(f'  updated: {fname}')

print(f'\nDone — {len(changed)} files updated')

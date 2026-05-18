"""Convert the Walmart SVG logo to a base64 PNG for email embedding.
Run from the project root: python _gen_logo.py
"""
import sys, os, io, base64, re

SVG_SRC  = r'C:\Users\ins0002\Downloads\walmart-logo-svgrepo-com (1).svg'
STATIC   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
SVG_DEST = os.path.join(STATIC, 'walmart-logo.svg')
OUT_PNG  = os.path.join(STATIC, 'walmart-logo.png')
OUT_B64  = os.path.join(STATIC, 'walmart-logo.b64')

os.makedirs(STATIC, exist_ok=True)

# ── 1. Fix SVG: strip root stroke/fill defaults that cause black rendering ─────
svg = open(SVG_SRC, encoding='utf-8').read()
svg = re.sub(r' stroke=["\']#000000["\']', '', svg)
svg = re.sub(r' fill=["\']#000000["\']',   '', svg)
with open(SVG_DEST, 'w', encoding='utf-8') as f:
    f.write(svg)
print(f'SVG saved: {SVG_DEST}')

# ── 2. Convert to PNG via svglib ───────────────────────────────────────────────
from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM

drawing = svg2rlg(SVG_DEST)
if not drawing:
    print('ERROR: svg2rlg returned None'); sys.exit(1)

print(f'SVG size: {drawing.width:.1f} x {drawing.height:.1f}')
TARGET_W = 200
scale          = TARGET_W / drawing.width
drawing.width  = TARGET_W
drawing.height = drawing.height * scale
drawing.transform = (scale, 0, 0, scale, 0, 0)

buf = io.BytesIO()
renderPM.drawToFile(drawing, buf, fmt='PNG', bg=0xFFFFFF)
png_bytes = buf.getvalue()

# ── Make white background transparent via Pillow ──────────────────────────────
from PIL import Image
import numpy as np
pil = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
arr = np.array(pil)
r, g, b, a = arr[...,0], arr[...,1], arr[...,2], arr[...,3]
white = (r > 240) & (g > 240) & (b > 240)
arr[..., 3][white] = 0
png_bytes = io.BytesIO()
Image.fromarray(arr).save(png_bytes, format='PNG')
png_bytes = png_bytes.getvalue()
print(f'PNG: {len(png_bytes)} bytes')

with open(OUT_PNG, 'wb') as f:
    f.write(png_bytes)

b64 = base64.b64encode(png_bytes).decode('ascii')
with open(OUT_B64, 'w') as f:
    f.write(b64)

print(f'Saved: {OUT_PNG}')
print(f'Saved: {OUT_B64}  ({len(b64)} chars)')
print('DONE')

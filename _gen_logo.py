"""Convert the Walmart SVG logo to a base64 PNG for email embedding."""
import sys, os, io, base64, math
sys.stdout.reconfigure(encoding='utf-8')

SVG_PATH = r'C:\Users\ins0002\Downloads\walmart-logo-svgrepo-com.svg'
OUT_B64  = os.path.join(os.path.dirname(__file__), 'static', 'walmart-logo.b64')
OUT_PNG  = os.path.join(os.path.dirname(__file__), 'static', 'walmart-logo.png')

os.makedirs(os.path.join(os.path.dirname(__file__), 'static'), exist_ok=True)

# ── Try svglib first ───────────────────────────────────────────────────────────
try:
    from svglib.svglib import svg2rlg
    from reportlab.graphics import renderPM

    drawing = svg2rlg(SVG_PATH)
    if not drawing:
        raise RuntimeError("svg2rlg returned None")

    print(f"SVG size: {drawing.width:.1f} x {drawing.height:.1f}")
    scale          = 180.0 / drawing.width
    drawing.width  = 180
    drawing.height = drawing.height * scale
    drawing.transform = (scale, 0, 0, scale, 0, 0)

    buf = io.BytesIO()
    renderPM.drawToFile(drawing, buf, fmt='PNG')
    png_bytes = buf.getvalue()
    print(f"PNG size: {len(png_bytes)} bytes")

except Exception as e:
    print(f"svglib failed: {e} — falling back to Pillow spark draw")

    # ── Fallback: draw the Walmart spark + wordmark with Pillow ───────────────
    from PIL import Image, ImageDraw, ImageFont

    W, H   = 200, 55
    YELLOW = (253, 187, 48)
    BLUE   = (26, 117, 207)
    img    = Image.new("RGBA", (W, H), (255, 255, 255, 0))
    draw   = ImageDraw.Draw(img)

    # Draw 6-petal spark at left side (each petal = rotated thin ellipse)
    cx, cy = 27, 27
    for angle in range(0, 180, 30):
        rad = math.radians(angle)
        # Create petal as a thin ellipse on a temp image, then paste rotated
        petal = Image.new("RGBA", (54, 54), (255, 255, 255, 0))
        pd    = ImageDraw.Draw(petal)
        pd.ellipse([20, 2, 34, 52], fill=YELLOW)
        rotated = petal.rotate(angle, expand=False)
        img.paste(rotated, (cx - 27, cy - 27), rotated)

    # Draw "Walmart" text
    try:
        font_lg = ImageFont.truetype("C:/Windows/Fonts/calibrib.ttf", 20)
        font_sm = ImageFont.truetype("C:/Windows/Fonts/calibri.ttf",  13)
    except Exception:
        font_lg = ImageFont.load_default()
        font_sm = font_lg

    draw.text((60, 4),  "Walmart",       font=font_lg, fill=BLUE)
    draw.text((60, 30), "Supply Chain",  font=font_sm, fill=BLUE)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    png_bytes = buf.getvalue()
    print(f"Pillow PNG size: {len(png_bytes)} bytes")

# ── Save outputs ──────────────────────────────────────────────────────────────
with open(OUT_PNG, 'wb') as f:
    f.write(png_bytes)
print(f"Saved PNG: {OUT_PNG}")

b64 = base64.b64encode(png_bytes).decode('ascii')
with open(OUT_B64, 'w') as f:
    f.write(b64)
print(f"Saved B64: {OUT_B64}  ({len(b64)} chars)")
print("DONE")

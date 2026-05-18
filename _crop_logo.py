"""Crop walmart-logo.png to its content bounding box (trim transparent padding),
then re-encode as base64 for the email signature.
Run from project root: python _crop_logo.py
"""
import os, io, base64
import numpy as np
from PIL import Image

STATIC  = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
IN_PNG  = os.path.join(STATIC, "walmart-logo.png")
OUT_PNG = os.path.join(STATIC, "walmart-logo.png")   # overwrite in-place
OUT_B64 = os.path.join(STATIC, "walmart-logo.b64")

img = Image.open(IN_PNG).convert("RGBA")
arr = np.array(img)

# Find rows and columns that contain at least one non-transparent pixel
alpha      = arr[..., 3]
rows_with  = np.any(alpha > 10, axis=1)
cols_with  = np.any(alpha > 10, axis=0)

top    = int(np.argmax(rows_with))
bottom = int(len(rows_with) - np.argmax(rows_with[::-1]))
left   = int(np.argmax(cols_with))
right  = int(len(cols_with) - np.argmax(cols_with[::-1]))

# Add a small uniform padding so the spark's tips aren't clipped
PAD = 12
top    = max(0, top    - PAD)
bottom = min(arr.shape[0], bottom + PAD)
left   = max(0, left   - PAD)
right  = min(arr.shape[1], right  + PAD)

cropped = img.crop((left, top, right, bottom))
print(f"Original : {img.size}")
print(f"Cropped  : {cropped.size}  (box: {left},{top} -> {right},{bottom})")

# Save cropped PNG
buf = io.BytesIO()
cropped.save(buf, format="PNG", optimize=True)
png_bytes = buf.getvalue()
print(f"PNG size : {len(png_bytes):,} bytes")

with open(OUT_PNG, "wb") as f:
    f.write(png_bytes)

b64 = base64.b64encode(png_bytes).decode("ascii")
with open(OUT_B64, "w") as f:
    f.write(b64)

print(f"Saved    : {OUT_PNG}")
print(f"Saved    : {OUT_B64}  ({len(b64):,} chars)")
print("DONE")

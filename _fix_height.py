"""
Fix the header height properly.
- overflow:hidden alone doesn't work when the container has no fixed height
- Need explicit height:72px on BOTH the header AND inner div
- Logo stays at 224px; flex items-center centers it so the wordmark
  sits in the visible 72px window while SVG whitespace gets clipped
"""
import glob, re

PAGES = glob.glob("templates/*.html")

for path in PAGES:
    txt = open(path, encoding="utf-8").read()
    orig = txt

    # 1. Give the header itself a fixed height (stops it expanding with content)
    txt = txt.replace(
        'class="bg-wm-blue sticky top-0 shadow-md" style="z-index:9999;overflow:hidden;"',
        'class="bg-wm-blue sticky top-0 shadow-md" style="z-index:9999; height:72px; overflow:hidden;"'
    )

    # 2. Change inner div: min-height:96px → height:72px + overflow:hidden
    txt = re.sub(
        r'(class="[^"]*flex items-center justify-between[^"]*") style="min-height:\d+px;"',
        r'\1 style="height:72px; overflow:hidden;"',
        txt
    )

    if txt != orig:
        open(path, "w", encoding="utf-8").write(txt)
        print(f"updated: {path}")

print("Done.")

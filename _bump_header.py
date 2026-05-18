"""
Bump logo 38px -> 56px, header min-height 56px -> 68px on every page.
Also upsize the title text on lead + tech (text-sm -> text-base).
"""
import glob, re

pages = glob.glob("templates/*.html")
for path in pages:
    txt = open(path, encoding="utf-8").read()
    orig = txt

    # 1. Logo size
    txt = txt.replace("height:38px;width:auto;", "height:56px;width:auto;")

    # 2. Header inner div height
    txt = txt.replace("min-height:56px;", "min-height:68px;")

    if txt != orig:
        open(path, "w", encoding="utf-8").write(txt)
        print(f"updated: {path}")

print("Base pass done.")

import glob

pages = glob.glob("templates/*.html")
for path in pages:
    txt = open(path, encoding="utf-8").read()
    orig = txt
    # Reduce header height 2.5x: 240 -> 96px
    txt = txt.replace("min-height:240px;", "min-height:96px;")
    # Clip the SVG whitespace overflow on the header itself
    txt = txt.replace(
        'class="bg-wm-blue sticky top-0 shadow-md" style="z-index:9999;"',
        'class="bg-wm-blue sticky top-0 shadow-md" style="z-index:9999;overflow:hidden;"'
    )
    if txt != orig:
        open(path, "w", encoding="utf-8").write(txt)
        print(f"updated: {path}")

print("Done.")

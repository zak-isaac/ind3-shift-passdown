import glob

pages = glob.glob("templates/*.html")
for path in pages:
    txt = open(path, encoding="utf-8").read()
    orig = txt
    txt = txt.replace("height:56px;width:auto;", "height:224px;width:auto;")
    txt = txt.replace("min-height:68px;", "min-height:240px;")
    if txt != orig:
        open(path, "w", encoding="utf-8").write(txt)
        print(f"updated: {path}")

print("Done.")

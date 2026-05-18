import glob

HOME_LINK = '<a href="/" class="text-blue-100 hover:text-white text-sm font-medium transition">&#127968; Home</a>'

# Pages that already have a home link or ARE home — skip home link injection
SKIP_HOME = {"home.html", "lead.html", "tech.html", "tech_login.html", "lead_login.html"}

pages = glob.glob("templates/*.html")
for path in pages:
    fname = path.split("\\")[-1].split("/")[-1]
    txt = open(path, encoding="utf-8").read()
    orig = txt

    # 1. Shrink all 80px logos to 38px
    txt = txt.replace('height:80px;width:auto;', 'height:38px;width:auto;')

    # 2. Add Home link if missing
    if fname not in SKIP_HOME:
        # Only inject if there's no existing home anchor
        if 'href="/"' not in txt or ('href="/"' in txt and 'Home' not in txt):
            # Find the right-side nav div and prepend Home before the first existing link
            # Strategy: find the closing </div> of the header's right flex container
            # and insert before the last link cluster
            # Simpler: inject after <div class="flex items-center gap-3"> (right side)
            # Each page has slightly different structure — use a safe anchor:
            # inject Home before the first ← or before </div>\n</header>
            for anchor in ['&#8592; Dashboard', '&#8592; History', '&#8592; Home',
                           '&#128203; Shifts', '&#8592;']:
                if anchor in txt:
                    txt = txt.replace(anchor, HOME_LINK + '\n      ' + anchor, 1)
                    break
            else:
                # shift_ended has no nav links at all — inject before </div>\n</header>
                txt = txt.replace(
                    '</div>\n</header>',
                    f'  {HOME_LINK}\n  </div>\n</header>',
                    1
                )

    if txt != orig:
        open(path, "w", encoding="utf-8").write(txt)
        print(f"updated: {fname}")

print("All done.")

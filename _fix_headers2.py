"""
Fix: the first pass inserted Home link INSIDE existing <a> tags.
This pass surgically repairs each affected file.
"""
import re

HOME = '<a href="/" class="text-blue-100 hover:text-white text-sm font-medium transition">&#127968; Home</a>'

fixes = {
    # file: [(bad_pattern, good_replacement), ...]
    "templates/analytics.html": [
        ('<a href="/lead" class="text-blue-200 hover:text-white text-sm font-medium transition">'
         + HOME + '\n      &#8592; Dashboard</a>',
         HOME + '\n      <a href="/lead" class="text-blue-200 hover:text-white text-sm font-medium transition">&#8592; Dashboard</a>'),
    ],
    "templates/asset_history.html": [
        ('<a href="/lead" class="text-blue-100 hover:text-white text-sm transition">'
         + HOME + '\n      &#8592; Dashboard</a>',
         HOME + '\n      <a href="/lead" class="text-blue-100 hover:text-white text-sm transition">&#8592; Dashboard</a>'),
    ],
    "templates/history.html": [
        ('<a href="/lead" class="text-blue-100 hover:text-white text-sm transition">'
         + HOME + '\n      &#8592; Dashboard</a>',
         HOME + '\n      <a href="/lead" class="text-blue-100 hover:text-white text-sm transition">&#8592; Dashboard</a>'),
    ],
    "templates/shift_report_view.html": [
        ('<a href="/history" class="text-blue-100 hover:text-white text-sm transition">'
         + HOME + '\n      &#8592; History</a>',
         HOME + '\n      <a href="/history" class="text-blue-100 hover:text-white text-sm transition">&#8592; History</a>'),
    ],
  "templates/weekly.html": [
        ('<a href="/lead" class="text-blue-100 hover:text-white text-sm transition">'
         + HOME + '\n      &#8592; Dashboard</a>',
         HOME + '\n      <a href="/lead" class="text-blue-100 hover:text-white text-sm transition">&#8592; Dashboard</a>'),
        # weekly uses &#8592; History not Dashboard, check both
        ('<a href="/history" class="text-blue-100 hover:text-white text-sm transition">'
         + HOME + '\n      &#8592;</a>',
         HOME + '\n      <a href="/history" class="text-blue-100 hover:text-white text-sm transition">&#8592;</a>'),
    ],
    "templates/print_qr.html": [
        ('<a href="/lead" class="text-blue-200 hover:text-white text-sm font-medium">'
         + HOME + '\n      &#8592; Dashboard</a>',
         HOME + '\n      <a href="/lead" class="text-blue-200 hover:text-white text-sm font-medium">&#8592; Dashboard</a>'),
    ],
    "templates/print_report.html": [
        ('<a href="/lead" class="text-blue-200 hover:text-white text-sm font-medium transition">'
         + HOME + '\n      &#8592; Dashboard</a>',
         HOME + '\n      <a href="/lead" class="text-blue-200 hover:text-white text-sm font-medium transition">&#8592; Dashboard</a>'),
    ],
}

for path, replacements in fixes.items():
    txt = open(path, encoding="utf-8").read()
    orig = txt
    for bad, good in replacements:
        txt = txt.replace(bad, good)
    if txt != orig:
        open(path, "w", encoding="utf-8").write(txt)
        print(f"fixed: {path}")
    else:
        print(f"no match (check manually): {path}")

print("Done.")

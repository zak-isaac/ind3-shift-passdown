import glob, sys
sys.stdout.reconfigure(encoding='utf-8')
templates = glob.glob('templates/*.html')
count = 0
for path in templates:
    txt  = open(path, encoding='utf-8').read()
    orig = txt
    # Main navbar logos: 52px -> 80px
    txt = txt.replace('walmart-logo-white.svg\" alt=\"Walmart\" style=\"height:52px',
                      'walmart-logo-white.svg\" alt=\"Walmart\" style=\"height:80px')
    # print_report: keep at a readable 44px (was 30px, still smaller than nav)
    txt = txt.replace('walmart-logo-white.svg\" alt=\"Walmart\" style=\"height:30px',
                      'walmart-logo-white.svg\" alt=\"Walmart\" style=\"height:44px')
    if txt != orig:
        open(path, 'w', encoding='utf-8').write(txt)
        print('updated:', path)
        count += 1
print(f'Done — {count} files updated')

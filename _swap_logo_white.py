import glob, sys
sys.stdout.reconfigure(encoding='utf-8')
templates = glob.glob('templates/*.html')
count = 0
for path in templates:
    txt = open(path, encoding='utf-8').read()
    if 'walmart-logo.svg' in txt and 'walmart-logo-white.svg' not in txt:
        fixed = txt.replace('walmart-logo.svg', 'walmart-logo-white.svg')
        open(path, 'w', encoding='utf-8').write(fixed)
        print('updated:', path)
        count += 1
print(f'Done: {count} files updated')

import glob, sys
sys.stdout.reconfigure(encoding='utf-8')
templates = glob.glob('templates/*.html')
count = 0
for path in templates:
    txt = open(path, encoding='utf-8').read()
    if 'walmart-logo-white.svg' in txt and 'height:36px' in txt:
        fixed = txt.replace('height:36px', 'height:52px')
        open(path, 'w', encoding='utf-8').write(fixed)
        print('updated:', path)
        count += 1
print(f'Done: {count} files updated')

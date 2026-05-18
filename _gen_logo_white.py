"""Generate white-text variant of Walmart logo for dark navbar use."""
import os

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
src  = os.path.join(STATIC, 'walmart-logo.svg')
dest = os.path.join(STATIC, 'walmart-logo-white.svg')

svg = open(src, encoding='utf-8').read()

print('chars in svg:', len(svg))
print('1a75cf count:', svg.count('1a75cf'))
print('fdbb30 count:', svg.count('fdbb30'))

# Replace the blue Walmart text paths with white
white_svg = svg.replace('#1a75cf', '#ffffff').replace('1a75cf', 'ffffff')

print('ffffff count after:', white_svg.count('ffffff'))
open(dest, 'w', encoding='utf-8').write(white_svg)
print(f'Saved: {dest}')

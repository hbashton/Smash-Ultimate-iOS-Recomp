import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # tools/, which this builds on
import sys, bisect; sys.path.insert(0,'tools')
from nso import Module
mod, *offs = sys.argv[1:]
import os
m = Module('dump/exefs/' + mod if os.path.exists('dump/exefs/' + mod) else 'dump/nro/' + mod + '.nro')
fs = sorted((s[1], s[0]) for s in m.symbols if s[1] and s[3] & 0xf == 2)
keys = [a for a, _ in fs]
for o in offs:
    o = int(o, 16); i = bisect.bisect_right(keys, o) - 1
    print(hex(o), fs[i][1] if i >= 0 else '?', '+', hex(o - keys[i]))

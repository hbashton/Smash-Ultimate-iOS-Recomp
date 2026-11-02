#!/usr/bin/env python3
"""List the direct calls a function makes, with imported (PLT) callees named.

Usage: calls.py <module> <offset hex> [--max N]
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # tools/, which this builds on
import bisect
import sys

import capstone

from funcs import import_stubs
from nso import Module

ROOT = __file__.rsplit("tools", 1)[0]


def main():
    mod_name, off = sys.argv[1], int(sys.argv[2], 16)
    limit = int(sys.argv[sys.argv.index("--max") + 1]) if "--max" in sys.argv else 4000
    m = Module(ROOT + "dump/exefs/" + mod_name)
    names = {s[1]: s[0] for s in m.symbols if s[1]}
    slots = {r[0]: m.symbols[r[2]][0] for r in m.relocs if r[2]}
    stubs = {stub: slots.get(slot, "?") for stub, slot in import_stubs(m).items()}
    starts = sorted(names)
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    for i in md.disasm(bytes(m.image[off:off + limit]), off):
        if i.mnemonic in ("bl", "b") and i.op_str.startswith("#"):
            t = int(i.op_str[1:], 16)
            k = bisect.bisect_right(starts, t) - 1
            label = stubs.get(t) or names.get(t) or (f"{names[starts[k]]}+{t - starts[k]:#x}" if k >= 0 else "")
            print(f"{i.address:#x} {i.mnemonic} {t:#x} {label}")
        if i.mnemonic == "ret" and "--all" not in sys.argv:
            break


if __name__ == "__main__":
    main()

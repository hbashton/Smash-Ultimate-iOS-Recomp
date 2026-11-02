#!/usr/bin/env python3
"""Disassemble a range of a module, optionally next to the lifter's C output.

Usage: dis.py <nso> <offset> [<size>] [--lift]
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # tools/, which this builds on
import sys

import capstone

from lift import BASE, Lifter
from nso import Module


def main():
    mod = Module(sys.argv[1])
    start = int(sys.argv[2], 16)
    size = int(sys.argv[3], 16) if len(sys.argv) > 3 and not sys.argv[3].startswith("--") else 0x80
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    cs.skipdata = True
    for addr, _sz, m, ops in cs.disasm_lite(bytes(mod.image[start:start + size]), start):
        print(f"{addr:08x}  {mod.u32(addr):08x}  {m:<8}{ops}")
    if "--lift" in sys.argv:
        code, why, _callees = Lifter(mod.image, {start}).lift_function(start, size)
        print(f"\n/* BASE = {BASE:#x}; unsupported: {sum(why.values())} */")
        print(code)


if __name__ == "__main__":
    main()

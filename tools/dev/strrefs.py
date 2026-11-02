#!/usr/bin/env python3
"""Print the C strings referenced (adrp + add/ldr) by code in an address range.

Usage: strrefs.py <module> <start hex> <end hex>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # tools/, which this builds on
import sys

import capstone

from nso import Module

ROOT = __file__.rsplit("tools", 1)[0]


def main():
    m = Module(ROOT + "dump/exefs/" + sys.argv[1])
    lo, hi = int(sys.argv[2], 16), int(sys.argv[3], 16)
    md = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    md.detail = True
    page = {}
    for i in md.disasm(bytes(m.image[lo:hi]), lo):
        ops = i.operands
        if i.mnemonic == "adrp":
            page[ops[0].reg] = ops[1].imm
        elif i.mnemonic == "add" and len(ops) == 3 and ops[1].reg in page and ops[2].type == capstone.arm64.ARM64_OP_IMM:
            a = page[ops[1].reg] + ops[2].imm
            if m.rodata.mem <= a < m.data.end:
                s = bytes(m.image[a:a + 120]).split(b"\0")[0]
                if len(s) >= 4 and all(32 <= b < 127 for b in s):
                    print(f"{i.address:#x}: {s.decode()}")


if __name__ == "__main__":
    main()

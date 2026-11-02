#!/usr/bin/env python3
"""Locate the game's CRC-32 table and the functions that reference it.

Usage: find_crc.py <nso>
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # tools/, which this builds on
import struct
import sys

from funcs import discover
from lift import sx
from nso import Module


def main():
    mod = Module(sys.argv[1])
    found, sizes = discover(mod)
    starts = sorted(a for a, s in found.items() if "eh" in s)

    image = bytes(mod.image)
    needle = struct.pack("<4I", 0x00000000, 0x77073096, 0xEE0E612C, 0x990951BA)
    tables = []
    pos = image.find(needle)
    while pos != -1:
        tables.append(pos)
        pos = image.find(needle, pos + 4)
    print("CRC-32 tables at:", ", ".join(f"{t:#x}" for t in tables) or "none")

    names = {v: n for n, v, _s, _i, _x in mod.exports}
    for a in starts:
        size = sizes[a]
        if size > 0x200:
            continue
        words = struct.unpack_from(f"<{size // 4}I", image, a)
        page = {}
        for i, w in enumerate(words):
            pc = a + i * 4
            if w & 0x9F000000 == 0x90000000:
                imm = sx((((w >> 5) & 0x7FFFF) << 2) | ((w >> 29) & 3), 21)
                page[w & 31] = (pc & ~0xFFF) + (imm << 12)
            elif w & 0xFFC00000 == 0x91000000 and (w >> 5) & 31 in page:
                if page[(w >> 5) & 31] + ((w >> 10) & 0xFFF) in tables:
                    out = sum(1 for x in words if x >> 26 in (0x25, 0x05) or x & 0xFFFFFC1F in (0xD63F0000, 0xD61F0000))
                    print(f"  f_{a:08x} size={size:#x} calls/branches={out} {names.get(a, '')}")
                    break


if __name__ == "__main__":
    main()

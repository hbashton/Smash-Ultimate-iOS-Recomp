#!/usr/bin/env python3
"""Symbolize "[host +0x...]" frames (runtime code offsets in ssbu.exe) with the linker map.

Usage: hostsym.py <ssbu.map> <log file or offsets...>
"""
import bisect
import re
import sys


def load(map_path, base=0x140000000):
    syms = []
    for line in open(map_path, errors="replace"):
        m = re.match(r"\s*[0-9a-f]{4}:[0-9a-f]{8}\s+(\S+)\s+([0-9a-f]{16})\s+(\S+)", line)
        if m:
            syms.append((int(m.group(2), 16) - base, m.group(1), m.group(3)))
    syms.sort()
    return syms


def name(syms, keys, off):
    i = bisect.bisect_right(keys, off) - 1
    return f"{syms[i][1]}+{off - syms[i][0]:#x} ({syms[i][2]})" if i >= 0 else "?"


def main():
    syms = load(sys.argv[1])
    keys = [s[0] for s in syms]
    args = sys.argv[2:]
    if len(args) == 1 and not args[0].startswith("0x"):
        for line in open(args[0], errors="replace"):
            m = re.search(r"\[host \+(0x[0-9a-f]+)\]", line)
            print(line.rstrip() + (f"  {name(syms, keys, int(m.group(1), 16))}" if m else ""))
    else:
        for a in args:
            print(a, name(syms, keys, int(a, 16)))


if __name__ == "__main__":
    main()

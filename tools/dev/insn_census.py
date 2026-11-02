#!/usr/bin/env python3
"""Instruction-level census of a module's text: what a static recompiler must translate.

Usage: insn_census.py <nso> [--jobs N]
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # tools/, which this builds on
import re
import sys
from collections import Counter
from multiprocessing import Pool

import capstone

from nso import Module

VEC = re.compile(r"\bv\d+\.|\b[qdshb]\d+\b|\bz\d+\b")
ATOMIC = re.compile(r"^(ldxr|ldaxr|stxr|stlxr|ldxp|ldaxp|stxp|stlxp|cas|swp|ldadd|ldclr|ldeor|ldset|ldsmax|ldsmin|ldumax|ldumin)")
CHUNK = 1 << 20


def work(args):
    data, base = args
    cs = capstone.Cs(capstone.CS_ARCH_ARM64, capstone.CS_MODE_ARM)
    cs.skipdata = True
    mn, sysreg, calls = Counter(), Counter(), set()
    vec = atomic = undecoded = 0
    for _addr, _size, m, ops in cs.disasm_lite(data, base):
        mn[m] += 1
        if m == "bl":
            calls.add(int(ops[1:], 16))
        elif m in ("mrs", "msr", "sys", "sysl"):
            sysreg[f"{m} {ops.split(',')[-1 if m == 'mrs' else 0].strip()}"] += 1
        elif m == ".byte":
            undecoded += 1
        if VEC.search(ops):
            vec += 1
        if ATOMIC.match(m):
            atomic += 1
    return mn, sysreg, calls, vec, atomic, undecoded


def main():
    mod = Module(sys.argv[1])
    jobs = int(sys.argv[sys.argv.index("--jobs") + 1]) if "--jobs" in sys.argv else 20
    text = bytes(mod.image[mod.text.mem:mod.text.end])
    tasks = [(text[o:o + CHUNK], mod.text.mem + o) for o in range(0, len(text), CHUNK)]
    mn, sysreg, calls = Counter(), Counter(), set()
    vec = atomic = undecoded = 0
    with Pool(jobs) as pool:
        for m, s, c, v, a, u in pool.imap_unordered(work, tasks):
            mn.update(m)
            sysreg.update(s)
            calls |= c
            vec += v
            atomic += a
            undecoded += u
    total = sum(mn.values())
    plt = [c for c in calls if not (mod.text.mem <= c < mod.text.end)]
    print(f"instructions: {total:,}   distinct mnemonics: {len(mn)}   undecodable words: {undecoded:,}")
    print(f"direct call targets (bl): {len(calls):,} distinct ({len(plt)} outside text)")
    print(f"calls: bl={mn['bl']:,}  blr={mn['blr']:,}   indirect jumps br={mn['br']:,}   ret={mn['ret']:,}")
    print(f"svc={mn['svc']}  brk={mn['brk']}  udf={mn['udf']}  hlt={mn['hlt']}")
    print(f"FP/SIMD-register instructions: {vec:,} ({vec * 100 / total:.1f}%)   atomics/exclusives: {atomic:,}")
    print("system register use: " + (", ".join(f"{k} x{v}" for k, v in sysreg.most_common(12)) or "none"))
    print("\ntop mnemonics:")
    for i, (m, n) in enumerate(mn.most_common(60)):
        print(f"  {m:<10}{n:>10,}", end="\n" if i % 4 == 3 else "")
    rare = sorted(m for m, n in mn.items() if n < 50)
    print(f"\n\nmnemonics seen fewer than 50 times ({len(rare)}): {' '.join(rare)}")


if __name__ == "__main__":
    main()

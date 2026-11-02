#!/usr/bin/env python3
"""Which SDK functions the game uses can end up talking to a system service?

Usage: ipc_reach.py <exefs dir>

Marks every SDK function that issues a service system call (connect to a named port, send a
request), propagates that backwards over direct calls, and lists the functions imported by
the game (and the other modules) that are marked and not yet replaced natively. Calls
through function pointers are not followed, so this is a lower bound - but a service session
is always opened through direct calls first, which is what matters here.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # tools/, which this builds on
import bisect
import os
import re
import struct
import sys
from collections import defaultdict

import recomp
from a64 import sx
from funcs import import_stubs

SERVICE_SVCS = {0x1F, 0x21, 0x22, 0x40, 0x41, 0x43, 0x44}


def main():
    exefs = sys.argv[1]
    mod, _found, funcs = recomp.load(os.path.join(exefs, "sdk"))
    starts = [a for a, _ in funcs]
    callers = defaultdict(set)
    seeds = set()
    # Calls to the module's own exports go through import stubs; see through them.
    by_name = {n: v for n, v, _s, info, _x in mod.exports if info & 0xF == 2}
    slot_name = {off: mod.symbols[sym][0] for off, typ, sym, _a in mod.relocs if typ == 1026}
    for stub, slot in import_stubs(mod).items():
        target = by_name.get(slot_name.get(slot))
        if target is not None:
            callers[target].add(stub)
    for start, size in funcs:
        words = struct.unpack_from(f"<{size // 4}I", mod.image, start)
        for i, w in enumerate(words):
            if w & 0xFFE0001F == 0xD4000001 and (w >> 5) & 0xFFFF in SERVICE_SVCS:
                seeds.add(start)
            elif w & 0x7C000000 == 0x14000000:   # b / bl
                t = start + 4 * (i + sx(w & 0x3FFFFFF, 26))
                if not start <= t < start + size:
                    k = bisect.bisect_right(starts, t) - 1
                    if k >= 0:
                        callers[funcs[k][0]].add(start)
    reach = set(seeds)
    todo = list(seeds)
    while todo:
        for caller in callers[todo.pop()]:
            if caller not in reach:
                reach.add(caller)
                todo.append(caller)

    exports = defaultdict(list)
    for name, value, _s, info, _x in mod.exports:
        if info & 0xF == 2:
            exports[value].append(name)
    replaced = recomp.hle_names()
    wanted = set()
    for other in ("main", "subsdk0"):
        wanted |= {s[0] for s in recomp.Module(os.path.join(exefs, other)).imports}
    by_ns = defaultdict(list)
    for value in reach:
        for name in exports.get(value, []):
            if name in wanted and name not in replaced:
                m = re.match(r"_ZN?K?(?:2nn)?(\d+)([A-Za-z_]+)", name)
                ns = m.group(2)[:int(m.group(1))] if m and name.startswith("_ZN2nn") else name.split("_")[0] or "(C)"
                by_ns[ns].append(name)
    total = sum(len(v) for v in by_ns.values())
    print(f"{len(seeds)} SDK functions issue service calls; {len(reach)} reach one through direct calls")
    print(f"{total} functions imported by other modules reach a service and are not replaced yet:")
    for ns, names in sorted(by_ns.items(), key=lambda kv: -len(kv[1])):
        print(f"  {ns} ({len(names)})")
        for n in sorted(names):
            print(f"      {n}")


if __name__ == "__main__":
    main()

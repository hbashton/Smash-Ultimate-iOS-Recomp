#!/usr/bin/env python3
"""Measure the boundary between the game executable and the Nintendo SDK.

Usage: census.py <exefs dir> [--list <namespace>]

Reports which symbols `main` imports, which module provides each, how they group by
namespace, and which NVN entry points the game looks up by name at runtime.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))   # tools/, which this builds on
import os
import re
import sys
from collections import Counter, defaultdict

from nso import Module


def scope(sym):
    """Best-effort namespace path of an Itanium-mangled symbol."""
    if not sym.startswith("_Z"):
        return ("(C)",)
    s = sym[2:]
    special = ""
    if s[:2] in ("TV", "TI", "TS", "TT"):
        special, s = s[:2], s[2:]
    if s.startswith("N"):
        i = 1
        while s[i] in "rVKRO":
            i += 1
        parts = []
        if s[i:i + 2] == "St":
            parts.append("std")
            i += 2
        while i < len(s) and s[i].isdigit():
            j = i
            while s[j].isdigit():
                j += 1
            n = int(s[i:j])
            parts.append(s[j:j + n])
            i = j + n
        return tuple(parts) if parts else ("(c++ other)",)
    if s[:1].isdigit():
        return ("(global c++)",)
    if s.startswith("St") or s.startswith("NSt"):
        return ("std",)
    return ("(c++ runtime)",) if not special else ("(c++ rtti)",)


def main():
    exefs = sys.argv[1]
    mods = {n: Module(os.path.join(exefs, n)) for n in sorted(os.listdir(exefs))
            if n == "main" or n == "rtld" or n.startswith("sdk") or n.startswith("subsdk")}
    game = mods["main"]
    providers = {}
    for name, m in mods.items():
        if name == "main":
            continue
        for s in m.exports:
            providers.setdefault(s[0], name)

    by_provider = Counter()
    groups = defaultdict(list)
    for s in game.imports:
        prov = providers.get(s[0], "UNRESOLVED")
        by_provider[prov] += 1
        sc = scope(s[0])
        key = "::".join(sc[:2]) if sc[0] == "nn" else sc[0]
        groups[key].append((s[0], prov))

    if "--list" in sys.argv:
        want = sys.argv[sys.argv.index("--list") + 1]
        for name, prov in sorted(groups.get(want, [])):
            print(f"{prov:<10} {name}")
        return

    print(f"main imports: {len(game.imports)}   providers: {dict(by_provider)}")
    print("\nimports by namespace:")
    for key, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        print(f"  {len(items):5}  {key}")

    # NVN is not linked by symbol: the game calls nvnBootstrapLoader / GetProcAddress with name strings.
    ro = bytes(game.image[game.rodata.mem:game.rodata.end])
    nvn = sorted(set(m.decode() for m in re.findall(rb"(?<![A-Za-z0-9_])nvn[A-Z][A-Za-z0-9]+(?=\0)", ro)))
    fam = Counter(re.match(r"nvn([A-Z][a-z]+(?:[A-Z][a-z]+)?)", n).group(1) for n in nvn)
    print(f"\nNVN entry points named in main's rodata: {len(nvn)}")
    print("  " + ", ".join(f"{k}={v}" for k, v in fam.most_common(24)))

    exp = Counter("::".join(scope(s[0])[:2]) for s in game.exports)
    print(f"\nmain exports (linked against by runtime-loaded NRO modules): {len(game.exports)}")
    for key, n in exp.most_common(15):
        print(f"  {n:5}  {key}")

    init = game.dyn.get(27, 0) // 8
    print(f"\nstatic constructors (.init_array entries): {init}")


if __name__ == "__main__":
    main()

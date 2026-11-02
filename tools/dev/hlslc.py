#!/usr/bin/env python3
"""Compile an HLSL file with the system D3DCompiler and print the diagnostics.

Usage: hlslc.py <file.hlsl> <target, e.g. vs_5_0> [entry]
"""
import ctypes
import sys


def compile_hlsl(src, target, entry="main"):
    d3d = ctypes.WinDLL("d3dcompiler_47.dll")
    code, errors = ctypes.c_void_p(), ctypes.c_void_p()
    data = src.encode()
    hr = d3d.D3DCompile(data, len(data), b"src", None, None, entry.encode(), target.encode(), 1 << 14, 0,
                        ctypes.byref(code), ctypes.byref(errors))
    text = ""
    if errors.value:
        vtbl = ctypes.cast(ctypes.cast(errors, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        get_ptr = ctypes.WINFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)(vtbl[3])
        text = ctypes.string_at(get_ptr(errors)).decode(errors="replace")
    return hr & 0xFFFFFFFF, text


if __name__ == "__main__":
    hr, text = compile_hlsl(open(sys.argv[1]).read(), sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "main")
    print(f"hr={hr:#x}")
    print(text)

#!/usr/bin/env python3
"""Turn a captured frame (24-bit top-down BMP from SSBU_CAPTURE) into a small PNG, and
summarise its colours.

Usage: shot.py <frame.bmp> <out.png> [--scale N]
"""
import struct
import sys
import zlib
from collections import Counter


def read_bmp(path):
    d = open(path, "rb").read()
    off, w, h = struct.unpack_from("<I", d, 10)[0], *struct.unpack_from("<ii", d, 18)
    top_down, h = h < 0, abs(h)
    row = (w * 3 + 3) & ~3
    rows = []
    for y in range(h):
        r = d[off + y * row: off + y * row + w * 3]
        rows.append([(r[x * 3 + 2], r[x * 3 + 1], r[x * 3]) for x in range(w)])
    return w, h, rows if top_down else rows[::-1]


def write_png(path, w, h, rows):
    raw = b"".join(b"\0" + bytes(c for px in r for c in px) for r in rows)
    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    open(path, "wb").write(png)


def main():
    src, dst = sys.argv[1], sys.argv[2]
    scale = int(sys.argv[sys.argv.index("--scale") + 1]) if "--scale" in sys.argv else 3
    w, h, rows = read_bmp(src)
    small = [r[::scale] for r in rows[::scale]]
    write_png(dst, len(small[0]), len(small), small)
    print(f"{w}x{h}; most common colours:", Counter(px for r in small for px in r).most_common(6))


if __name__ == "__main__":
    main()

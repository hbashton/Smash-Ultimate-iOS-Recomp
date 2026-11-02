#!/usr/bin/env python3
"""Compare two runs' captures (wintest_run.sh): for each frame_N.bmp both have, the mean
difference per pixel, and a side-by-side PNG (a | b, half size) to look at.

Usage: compare_shots.py <run a dir> <run b dir> <out dir>"""
import os
import struct
import sys
import zlib


def read_bmp(path):
    d = open(path, "rb").read()
    off, w, h, bpp = struct.unpack_from("<I", d, 10)[0], *struct.unpack_from("<iiHH", d, 18)[:2], struct.unpack_from("<H", d, 28)[0]
    top_down = h < 0
    h = abs(h)
    row = (w * 3 + 3) & ~3
    rows = [d[off + y * row: off + y * row + w * 3] for y in range(h)]
    if not top_down:
        rows.reverse()
    return w, h, rows   # BGR rows


def write_png(path, w, h, rows_rgb):
    raw = b"".join(b"\0" + r for r in rows_rgb)
    def chunk(t, data):
        c = struct.pack(">I", len(data)) + t + data
        return c + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")
    open(path, "wb").write(png)


def half(w, h, rows):
    out = []
    for y in range(0, h - 1, 2):
        r = rows[y]
        line = bytearray()
        for x in range(0, w - 1, 2):
            b, g, rr = r[x * 3], r[x * 3 + 1], r[x * 3 + 2]
            line += bytes((rr, g, b))
        out.append(bytes(line))
    return w // 2, len(out), out


def main():
    a, b, out = sys.argv[1:4]
    os.makedirs(out, exist_ok=True)
    names = sorted(set(os.listdir(os.path.join(a, "shots"))) & set(os.listdir(os.path.join(b, "shots"))))
    for n in names:
        if not n.endswith(".bmp"):
            continue
        wa, ha, ra = read_bmp(os.path.join(a, "shots", n))
        wb, hb, rb = read_bmp(os.path.join(b, "shots", n))
        if (wa, ha) != (wb, hb):
            print(f"{n}: sizes differ")
            continue
        total = 0
        for y in range(0, ha, 4):   # (every 4th row: plenty for a mean)
            x, z = ra[y], rb[y]
            total += sum(abs(x[i] - z[i]) for i in range(0, len(x), 3))
        mean = total / ((ha // 4) * wa)
        w2, h2, ha2 = half(wa, ha, ra)
        _, _, hb2 = half(wb, hb, rb)
        write_png(os.path.join(out, n[:-4] + ".png"), w2 * 2, h2, [x + y for x, y in zip(ha2, hb2)])
        print(f"{n}: mean difference {mean:.2f} (blue channel, 0-255)")


if __name__ == "__main__":
    main()

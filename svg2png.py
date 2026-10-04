#!/usr/bin/env python3
"""svg2png - rasterise the SVG subset that FSVG programs emit.

Exists because the box has no SVG renderer: ImageMagick's built-in MSVG
rejects `polyline` ("non-conforming drawing primitive"), rsvg-convert and
cairosvg are not installed, and there is no browser. So this draws exactly what
FSVG output uses - <rect>, <polyline>, and a <path> of M/L commands - into a
PNG written with zlib. No dependencies beyond the standard library.
"""
import re
import struct
import sys
import zlib


def parse_attrs(tag):
    # FSVG emits single-quoted attributes (they come from the program's own
    # strings), so accept both quote styles - a double-quote-only regex matched
    # nothing and the picture came out empty with "0 points drawn".
    return dict((k, v) for k, q, v in
                re.findall(r"([\w:-]+)\s*=\s*(['\"])(.*?)\2", tag))


def hexcol(s, default=(13, 17, 23)):
    s = (s or "").strip()
    if not s or s in ("none", "transparent"):
        return None
    m = re.fullmatch(r"#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})", s)
    if m:
        h = m.group(1)
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return default


class Canvas:
    def __init__(self, w, h, bg=(13, 17, 23)):
        self.w, self.h = w, h
        self.px = bytearray()
        for _ in range(w * h):
            self.px += bytes(bg)

    def blend(self, x, y, col, a=1.0):
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = (y * self.w + x) * 3
        for k in range(3):
            self.px[i + k] = int(self.px[i + k] * (1 - a) + col[k] * a)

    def rect(self, x, y, w, h, col):
        for yy in range(max(0, y), min(self.h, y + h)):
            for xx in range(max(0, x), min(self.w, x + w)):
                self.blend(xx, yy, col)

    def line(self, x0, y0, x1, y1, col, width=1):
        """Bresenham, thickened by stamping a width x width square."""
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx = 1 if x0 < x1 else -1
        sy = 1 if y0 < y1 else -1
        err = dx + dy
        r = max(0, width // 2)
        while True:
            for oy in range(-r, r + 1):
                for ox in range(-r, r + 1):
                    self.blend(x0 + ox, y0 + oy, col)
            if x0 == x1 and y0 == y1:
                break
            e2 = 2 * err
            if e2 >= dy:
                err += dy
                x0 += sx
            if e2 <= dx:
                err += dx
                y0 += sy

    def png(self):
        raw = b"".join(b"\x00" + bytes(self.px[y * self.w * 3:(y + 1) * self.w * 3])
                       for y in range(self.h))

        def chunk(tag, data):
            c = tag + data
            return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c))
        return (b"\x89PNG\r\n\x1a\n"
                + chunk(b"IHDR", struct.pack(">IIBBBBB", self.w, self.h, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9))
                + chunk(b"IEND", b""))


def num(v, d=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def render(svg_text, out_png):
    m = re.search(r"<svg[^>]*\bwidth=['\"](\d+)['\"][^>]*\bheight=['\"](\d+)['\"]", svg_text)
    w, h = (int(m.group(1)), int(m.group(2))) if m else (900, 520)
    cv = Canvas(w, h)

    drawn = 0
    for tag in re.findall(r"<(?:rect|polyline|path)\b[^>]*>", svg_text):
        a = parse_attrs(tag)
        kind = tag[1:].split()[0].split(">")[0]
        if kind == "rect":
            col = hexcol(a.get("fill"))
            if col:
                cv.rect(int(num(a.get("x"))), int(num(a.get("y"))),
                        int(num(a.get("width"))), int(num(a.get("height"))), col)
            continue

        col = hexcol(a.get("stroke")) or hexcol(a.get("fill"))
        if not col:
            continue
        wpx = max(1, int(num(a.get("stroke-width"), 1)))
        if kind == "polyline":
            nums = [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?", a.get("points", ""))]
            pts = list(zip(nums[0::2], nums[1::2]))
        else:
            d = a.get("d", "")
            pts = [(float(x), float(y))
                   for x, y in re.findall(r"[ML]\s*(-?\d+(?:\.\d+)?)[ ,]+(-?\d+(?:\.\d+)?)", d)]
            pts = [(p[0], p[1]) for p in pts]
        drawn = max(drawn, len(pts))
        for i in range(len(pts) - 1):
            cv.line(int(pts[i][0]), int(pts[i][1]),
                    int(pts[i + 1][0]), int(pts[i + 1][1]), col, wpx)
    open(out_png, "wb").write(cv.png())
    return w, h, drawn


if __name__ == "__main__":
    src = open(sys.argv[1]).read()
    w, h, n = render(src, sys.argv[2])
    print(f"{sys.argv[2]}  {w}x{h}  {n} points drawn")
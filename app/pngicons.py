"""Draws the app's home-screen icons as PNG files with the standard library only (no image libraries).

The icons are made on first request and cached by the caller.
"""
from __future__ import annotations

import math
import struct
import zlib


def draw_icon(size: int, scale: float) -> bytes:
    """The Watcher's great eye on a deep-space square, as a PNG (no image libraries needed)."""
    ss = 2
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            acc = [0.0, 0.0, 0.0]
            for sy in range(ss):
                for sx in range(ss):
                    u = ((x + (sx + 0.5) / ss) / size - 0.5) / scale
                    v = ((y + (sy + 0.5) / ss) / size - 0.5) / scale
                    r2 = u * u + v * v
                    g = math.exp(-r2 / 0.07)
                    col = [7 + 105 * 0.5 * g, 9 + 75 * 0.5 * g, 26 + 255 * 0.34 * g]
                    ax = abs(u)
                    lid = 0.21 * (1 - (ax / 0.40) ** 2) if ax < 0.40 else -1.0
                    if lid > 0:
                        d = lid - abs(v)
                        if d > 0.012:
                            col = [10, 12, 30]
                            ir = math.hypot(u, v * 1.0)
                            if ir < 0.15:
                                t = ir / 0.15
                                col = [255 * (1 - 0.15 * t) , 130 + 110 * (1 - t), 195 + 60 * (1 - t)]
                                if abs(u) < 0.022 and abs(v) < 0.125:
                                    col = [6, 4, 14]
                        elif d > -0.01:
                            col = [175, 140, 255]
                    elif lid > -1.0 and abs(v) < 0.012 + 0.0 and ax < 0.42:
                        col = [150, 120, 255]
                    # a small pink bow on top
                    bx, by = u - 0.0, v + 0.26
                    if abs(by) < 0.07 and ((abs(bx) - 0.03) / 0.10) ** 2 + (by / 0.065) ** 2 < 1 and abs(bx) > 0.012:
                        col = [255, 111, 181]
                    elif bx * bx + by * by < 0.0007:
                        col = [214, 70, 140]
                    for i in range(3):
                        acc[i] += col[i]
            for i in range(3):
                rows.append(max(0, min(255, int(acc[i] / (ss * ss)))))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")


def draw_badge(size: int = 96) -> bytes:
    """A white eye on a transparent background: the status-bar badge Android expects."""
    ss = 3
    rows = bytearray()
    for y in range(size):
        rows.append(0)
        for x in range(size):
            cov = 0
            for sy in range(ss):
                for sx in range(ss):
                    u = ((x + (sx + 0.5) / ss) / size - 0.5) / 0.92
                    v = ((y + (sy + 0.5) / ss) / size - 0.5) / 0.92
                    ax = abs(u)
                    lid = 0.24 * (1 - (ax / 0.46) ** 2) if ax < 0.46 else -1.0
                    inside = False
                    if lid > 0:
                        d = lid - abs(v)
                        ring = 0 < d < 0.06
                        iris = math.hypot(u, v) < 0.17 and not (abs(u) < 0.028 and abs(v) < 0.14)
                        inside = ring or iris
                    if inside:
                        cov += 1
            rows += bytes((255, 255, 255, int(255 * cov / (ss * ss))))

    def chunk(tag: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xFFFFFFFF)

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(bytes(rows), 9)) + chunk(b"IEND", b"")

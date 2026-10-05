"""Generate the Alexa+ add-on media assets (icons and carousel image) as PNGs. Standard library only.

    python scripts/make_alexa_assets.py

Writes alexa/assets/icon-<W>x<H>.png for every size addon.json requires, plus carousel-600x900.png.
The art is a simple shield (the "aegis") on a calm blue gradient. Replace it with designed artwork
before certification if you like; the sizes and file names must stay the same.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "alexa" / "assets"
ICON_SIZES = (72, 64, 88, 126, 180, 241)
CAROUSEL = (600, 900)

TOP = (13, 27, 42)        # deep navy
BOTTOM = (27, 73, 101)    # ocean blue
SHIELD = (244, 246, 251)  # near white
MARK = (220, 38, 38)      # warning red


def _png(width: int, height: int, rows: list[bytes]) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + row for row in rows)  # filter type 0 on every scanline
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def _inside_shield(x: float, y: float) -> bool:
    """Unit-square shield: straight top, sides curving into a point at the bottom."""
    if not (0.18 <= y <= 0.86):
        return False
    if y <= 0.55:
        half = 0.34
    else:
        t = (y - 0.55) / (0.86 - 0.55)
        half = 0.34 * (1 - t * t)
    return abs(x - 0.5) <= half


def _inside_mark(x: float, y: float) -> bool:
    """A check mark inside the shield: protection that asks first."""
    def near(ax: float, ay: float, bx: float, by: float, width: float) -> bool:
        dx, dy = bx - ax, by - ay
        t = max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / (dx * dx + dy * dy)))
        px, py = ax + t * dx, ay + t * dy
        return (x - px) ** 2 + (y - py) ** 2 <= width * width

    return near(0.37, 0.50, 0.47, 0.62, 0.035) or near(0.47, 0.62, 0.65, 0.38, 0.035)


def render(width: int, height: int) -> bytes:
    size = min(width, height)
    ox, oy = (width - size) / 2, (height - size) / 2
    rows = []
    for py in range(height):
        f = py / max(height - 1, 1)
        background = bytes(round(TOP[i] + (BOTTOM[i] - TOP[i]) * f) for i in range(3))
        row = bytearray()
        for px in range(width):
            x, y = (px - ox + 0.5) / size, (py - oy + 0.5) / size
            if 0 <= x <= 1 and 0 <= y <= 1 and _inside_mark(x, y):
                row += bytes(MARK)
            elif 0 <= x <= 1 and 0 <= y <= 1 and _inside_shield(x, y):
                row += bytes(SHIELD)
            else:
                row += background
        rows.append(bytes(row))
    return _png(width, height, rows)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for side in ICON_SIZES:
        (OUT / f"icon-{side}x{side}.png").write_bytes(render(side, side))
    (OUT / f"carousel-{CAROUSEL[0]}x{CAROUSEL[1]}.png").write_bytes(render(*CAROUSEL))
    print(f"wrote {len(ICON_SIZES) + 1} PNGs to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()

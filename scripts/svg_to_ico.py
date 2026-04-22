"""Convert an SVG into a multi-resolution .ico file.

Uses only PyQt6 + stdlib so no extra dependencies are needed.

Usage:
    python scripts/svg_to_ico.py src/assets/taskbar-monitor.svg src/assets/taskbar-monitor.ico
"""

from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, Qt
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

ICON_SIZES = (16, 32, 48, 64, 128, 256)


def _rasterize(renderer: QSvgRenderer, size: int) -> bytes:
    """Rasterize an SVG to a transparent PNG at size x size pixels."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    renderer.render(painter)
    painter.end()

    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    ok = image.save(buf, "PNG")
    if not ok:
        raise RuntimeError(f"Failed to encode PNG at size {size}")
    data = bytes(buf.data())
    buf.close()
    return data


def _pack_ico(png_blobs: list[tuple[int, bytes]]) -> bytes:
    """Pack a list of (size, png_bytes) tuples into an .ico container."""
    header = struct.pack("<HHH", 0, 1, len(png_blobs))  # reserved, type=1 (icon), count
    entries = io.BytesIO()
    images = io.BytesIO()

    offset = len(header) + 16 * len(png_blobs)
    for size, data in png_blobs:
        w = 0 if size >= 256 else size  # ICO uses 0 to mean 256
        h = 0 if size >= 256 else size
        entries.write(struct.pack(
            "<BBBBHHII",
            w, h,
            0,   # colors in palette (0 for truecolor)
            0,   # reserved
            1,   # color planes
            32,  # bits per pixel
            len(data),
            offset,
        ))
        images.write(data)
        offset += len(data)

    return header + entries.getvalue() + images.getvalue()


def convert(svg_path: Path, ico_path: Path) -> None:
    """Convert an SVG file to a multi-size .ico file."""
    if not svg_path.exists():
        raise FileNotFoundError(svg_path)

    # QImage/QPainter require a QApplication (even offscreen)
    app = QApplication.instance() or QApplication(["svg_to_ico"])
    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        raise RuntimeError(f"Invalid SVG: {svg_path}")

    blobs = [(size, _rasterize(renderer, size)) for size in ICON_SIZES]
    ico_path.parent.mkdir(parents=True, exist_ok=True)
    ico_path.write_bytes(_pack_ico(blobs))
    print(f"Wrote {ico_path} ({ico_path.stat().st_size} bytes, sizes={list(ICON_SIZES)})")
    del app  # silence 'unused' warning in some linters


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: svg_to_ico.py <input.svg> <output.ico>")
        return 2
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

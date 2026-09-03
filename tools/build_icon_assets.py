"""Build the deterministic PNG and multi-resolution ICO brand assets."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

PALETTE = ("#D9633F", "#718B68", "#D99A28", "#4778A5")


def render_icon(size: int) -> QImage:
    scale = size / 1024
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(scale, scale)

    tile = QPainterPath()
    tile.addRoundedRect(QRectF(48, 48, 928, 928), 190, 190)
    painter.fillPath(tile, QColor("#FFF8EA"))

    positions = ((166, 166), (548, 166), (166, 548), (548, 548))
    for (x, y), color in zip(positions, PALETTE, strict=True):
        block = QPainterPath()
        block.addRoundedRect(QRectF(x, y, 310, 310), 78, 78)
        painter.fillPath(block, QColor(color))
        painter.setPen(QPen(QColor("#FFF8EA"), 30, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawLine(x + 78, y + 155, x + 232, y + 155)

    painter.save()
    painter.translate(512, 512)
    painter.rotate(45)
    center = QPainterPath()
    center.addRoundedRect(QRectF(-72, -72, 144, 144), 28, 28)
    painter.fillPath(center, QColor("#35322F"))
    painter.restore()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#FFF8EA"))
    painter.drawEllipse(QRectF(490, 490, 44, 44))
    painter.end()
    return image


def png_bytes(image: QImage) -> bytes:
    payload = QByteArray()
    buffer = QBuffer(payload)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not image.save(buffer, "PNG"):
        raise RuntimeError("Could not encode icon PNG")
    return bytes(payload)


def write_ico(path: Path, sizes: tuple[int, ...]) -> None:
    images = [(size, png_bytes(render_icon(size))) for size in sizes]
    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + 16 * len(images)
    entries: list[bytes] = []
    payloads: list[bytes] = []
    for size, payload in images:
        dimension = 0 if size == 256 else size
        entries.append(struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(payload), offset))
        payloads.append(payload)
        offset += len(payload)
    path.write_bytes(header + b"".join(entries) + b"".join(payloads))


def main() -> int:
    destination = Path(sys.argv[1] if len(sys.argv) > 1 else "resources").resolve()
    destination.mkdir(parents=True, exist_ok=True)
    if not render_icon(1024).save(str(destination / "four-ai-consult.png"), "PNG"):
        raise RuntimeError("Could not save the PNG icon")
    write_ico(destination / "four-ai-consult.ico", (16, 20, 24, 32, 40, 48, 64, 128, 256))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

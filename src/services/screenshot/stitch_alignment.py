"""Image similarity and vertical stitch alignment."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage, QPainter

LOGGER = logging.getLogger(__name__)

def are_images_similar(img1: QImage, img2: QImage, threshold: float = 0.997) -> bool:
    """Compare two images on a small grid."""
    if img1.size() != img2.size():
        return False

    width = img1.width()
    height = img1.height()
    if width <= 0 or height <= 0:
        return True

    x_step = max(1, width // 96)
    y_step = max(1, height // 96)
    diff = 0
    max_diff = 0
    for y in range(0, height, y_step):
        for x in range(0, width, x_step):
            left = img1.pixelColor(x, y)
            right = img2.pixelColor(x, y)
            diff += (
                abs(left.red() - right.red())
                + abs(left.green() - right.green())
                + abs(left.blue() - right.blue())
            )
            max_diff += 3 * 255

    similarity = 1.0 - (diff / max(1, max_diff))
    return similarity >= threshold


def _row_signatures(image: QImage, columns: list[int]) -> list[tuple[int, ...]]:
    signatures: list[tuple[int, ...]] = []
    for y in range(image.height()):
        row: list[int] = []
        for x in columns:
            value = image.pixel(x, y)
            row.extend(((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))
        signatures.append(tuple(row))
    return signatures


def _column_signatures(image: QImage, rows: list[int]) -> list[tuple[int, ...]]:
    """Per-column signatures sampled over representative rows (X-axis mirror)."""
    signatures: list[tuple[int, ...]] = []
    for x in range(image.width()):
        column: list[int] = []
        for y in rows:
            value = image.pixel(x, y)
            column.extend(((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))
        signatures.append(tuple(column))
    return signatures


def _sample_lines(perpendicular: int) -> list[int]:
    """Evenly spaced sample positions along the axis perpendicular to scroll."""
    count = min(33, max(9, perpendicular // 45))
    return [
        min(perpendicular - 1, round((index + 1) * perpendicular / (count + 1)))
        for index in range(count)
    ]


def _axis_offset(
    sigs1: list[tuple[int, ...]],
    sigs2: list[tuple[int, ...]],
    axis_len: int,
    expected_offset: int | None,
) -> int:
    """Find the scroll offset of sigs2 relative to sigs1 along one axis.

    Shared by find_vertical_offset and find_horizontal_offset: the only
    axis-specific work is building the signature lists.
    """
    min_d = 1
    max_d = min(axis_len - 1, int(axis_len * 0.96))
    if max_d <= min_d:
        return _offset_fallback(axis_len, expected_offset)

    sample_step = max(2, axis_len // 180)

    def score(d: int) -> float:
        overlap = axis_len - d
        if overlap <= 0:
            return float("inf")
        diff = 0
        comparisons = 0
        for pos in range(0, overlap, sample_step):
            a = sigs1[d + pos]
            b = sigs2[pos]
            diff += sum(abs(left - right) for left, right in zip(a, b))
            comparisons += len(a)
        return diff / max(1, comparisons)

    # Score every candidate shift. The true alignment can be a 1px-sharp
    # minimum that a coarse grid straddles and misses, so it is scanned at
    # full resolution.
    scores = [score(d) for d in range(min_d, max_d + 1)]
    best_score = min(scores)

    # Prefer the SMALLEST shift that aligns essentially as well as the best.
    # The average-diff metric structurally favors large d — a tiny overlap has
    # few lines left to disagree on, so spurious near-edge matches score low
    # and over-estimate the offset, duplicating content at the first and last
    # seams. The real shift is the smallest near-perfect alignment.
    tolerance = max(2.0, best_score * 0.4)
    refined = min_d
    chosen_score = best_score
    for index, value in enumerate(scores):
        if value <= best_score + tolerance:
            refined = min_d + index
            chosen_score = value
            break

    if chosen_score < 18.0:
        return refined

    fallback = _offset_fallback(axis_len, expected_offset)
    LOGGER.warning(
        "Stitching: best match had high difference: %.2f (d=%s). Using fallback %d.",
        chosen_score,
        refined,
        fallback,
    )
    return fallback


def _offset_fallback(height: int, expected_offset: int | None) -> int:
    """Best-guess scroll offset when content matching is not conclusive."""
    if expected_offset is not None:
        return min(max(1, expected_offset), max(1, height - 1))
    return max(1, int(height * 0.8))


def _stride_notches(
    frame_height: int,
    px_per_notch: float | None,
    margin_px: int,
    fallback_notches: int,
) -> int:
    """Wheel notches needed to advance nearly one viewport per scroll step.

    Until pixels-per-notch has been calibrated from a measured frame shift,
    a small fixed step is used so the first stitch can act as calibration.
    """
    if px_per_notch is None or px_per_notch <= 0 or frame_height <= 0:
        return fallback_notches
    margin = max(margin_px, int(frame_height * 0.08))
    target = frame_height - margin
    if target <= 0:
        return fallback_notches
    return max(1, min(200, int(target / px_per_notch)))


def find_vertical_offset(
    img1: QImage,
    img2: QImage,
    expected_offset: int | None = None,
) -> int | None:
    """Find the vertical pixel offset of img2 relative to img1."""
    height = img1.height()
    width = img1.width()
    if img1.size() != img2.size():
        return None
    if height <= 50 or width <= 50:
        return _offset_fallback(height, expected_offset)

    columns = _sample_lines(width)
    sigs1 = _row_signatures(img1, columns)
    sigs2 = _row_signatures(img2, columns)
    return _axis_offset(sigs1, sigs2, height, expected_offset)


def find_horizontal_offset(
    img1: QImage,
    img2: QImage,
    expected_offset: int | None = None,
) -> int | None:
    """Find the horizontal pixel offset (dx) of img2 relative to img1.

    img2 shows content scrolled LEFT relative to img1: it reveals content
    that is ``dx`` pixels further to the right. Mirrors find_vertical_offset
    along the X axis, sampling per-column signatures over representative rows.
    """
    height = img1.height()
    width = img1.width()
    if img1.size() != img2.size():
        return None
    if height <= 50 or width <= 50:
        return _offset_fallback(width, expected_offset)

    rows = _sample_lines(height)
    sigs1 = _column_signatures(img1, rows)
    sigs2 = _column_signatures(img2, rows)
    return _axis_offset(sigs1, sigs2, width, expected_offset)


def stitch_images(images: list[QImage], offsets: list[int]) -> QImage | None:
    """Stitch a sequence of images together using calculated offsets."""
    if not images:
        return None
    if len(images) == 1:
        return images[0]

    width = images[0].width()
    height = images[0].height()
    total_height = height + sum(offsets)

    stitched = QImage(width, total_height, QImage.Format.Format_ARGB32)
    stitched.fill(0)

    painter = QPainter(stitched)
    painter.drawImage(0, 0, images[0])

    y = height
    for index, offset in enumerate(offsets):
        append_height = max(1, min(offset, images[index + 1].height()))
        source_y = max(0, images[index + 1].height() - append_height)
        painter.drawImage(
            QRect(0, y, width, append_height),
            images[index + 1],
            QRect(0, source_y, width, append_height),
        )
        y += append_height

    painter.end()
    return stitched


def stitch_images_horizontal(
    images: list[QImage],
    offsets: list[int],
) -> QImage | None:
    """Stitch a sequence of images left-to-right using calculated offsets.

    The X-axis mirror of stitch_images: each subsequent image contributes a
    strip of ``offset`` columns appended to the right edge of the canvas.
    """
    if not images:
        return None
    if len(images) == 1:
        return images[0]

    width = images[0].width()
    height = images[0].height()
    total_width = width + sum(offsets)

    stitched = QImage(total_width, height, QImage.Format.Format_ARGB32)
    stitched.fill(0)

    painter = QPainter(stitched)
    painter.drawImage(0, 0, images[0])

    x = width
    for index, offset in enumerate(offsets):
        append_width = max(1, min(offset, images[index + 1].width()))
        source_x = max(0, images[index + 1].width() - append_width)
        painter.drawImage(
            QRect(x, 0, append_width, height),
            images[index + 1],
            QRect(source_x, 0, append_width, height),
        )
        x += append_width

    painter.end()
    return stitched

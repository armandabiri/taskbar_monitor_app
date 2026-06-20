"""Headless stitch regression checks (CI-safe, no Win32 capture)."""

from PyQt6.QtGui import QColor, QImage

from services.screenshot_service import find_vertical_offset, stitch_images


def _frame_with_band(height: int, band_y: int, color: QColor) -> QImage:
    image = QImage(80, height, QImage.Format.Format_ARGB32)
    image.fill(QColor("white").rgb())
    for x in range(80):
        image.setPixel(x, band_y, color.rgb())
    return image


def test_stitch_vertical_band_sequence_matches_offsets() -> None:
    """Two frames with a band shifted by 30px stitch to height 130."""
    img1 = _frame_with_band(100, 70, QColor("red"))
    img2 = _frame_with_band(100, 40, QColor("red"))
    offset = find_vertical_offset(img1, img2)
    assert offset == 30
    stitched = stitch_images([img1, img2], [offset])
    assert stitched is not None
    assert stitched.height() == 130
    assert stitched.width() == 80


def test_stitch_three_frame_chain() -> None:
    heights = []
    frames = [_frame_with_band(100, 80, QColor("black"))]
    offsets: list[int] = []
    for step in (25, 20):
        prev = frames[-1]
        next_frame = _frame_with_band(100, 80 - step, QColor("black"))
        offset = find_vertical_offset(prev, next_frame)
        assert offset is not None
        offsets.append(offset)
        frames.append(next_frame)
        heights.append(offset)
    stitched = stitch_images(frames, offsets)
    assert stitched is not None
    assert stitched.height() == 100 + sum(heights)

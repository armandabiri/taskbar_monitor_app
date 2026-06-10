from PyQt6.QtGui import QColor, QImage

from services.screenshot_service import (
    _offset_fallback,
    _pack_signed_words,
    _stride_notches,
    are_images_similar,
    find_vertical_offset,
    stitch_images,
)


def test_are_images_similar_identical() -> None:
    # Two identical images filled with white
    img1 = QImage(100, 100, QImage.Format.Format_ARGB32)
    img1.fill(QColor("white").rgb())

    img2 = QImage(100, 100, QImage.Format.Format_ARGB32)
    img2.fill(QColor("white").rgb())

    assert are_images_similar(img1, img2)


def test_are_images_similar_different() -> None:
    # One white, one black image
    img1 = QImage(100, 100, QImage.Format.Format_ARGB32)
    img1.fill(QColor("white").rgb())

    img2 = QImage(100, 100, QImage.Format.Format_ARGB32)
    img2.fill(QColor("black").rgb())

    assert not are_images_similar(img1, img2)


def test_find_vertical_offset() -> None:
    # Create two images representing a vertical scroll shift of 40 pixels
    # We will draw a distinctive horizontal line
    img1 = QImage(100, 100, QImage.Format.Format_ARGB32)
    img1.fill(QColor("white").rgb())
    # Line at y=60 in img1
    for x in range(100):
        img1.setPixel(x, 60, QColor("red").rgb())

    img2 = QImage(100, 100, QImage.Format.Format_ARGB32)
    img2.fill(QColor("white").rgb())
    # The same line shifted up by 40px (so at y=20) in img2
    for x in range(100):
        img2.setPixel(x, 20, QColor("red").rgb())

    offset = find_vertical_offset(img1, img2)
    assert offset == 40


def test_pack_signed_words_wheel_deltas() -> None:
    # Scroll down: high word carries -120 as two's complement
    assert _pack_signed_words(0, -120) == 0xFF880000
    # Scroll up: high word carries +120
    assert _pack_signed_words(0, 120) == 0x00780000
    # Point packing: low word x, high word y
    assert _pack_signed_words(700, 450) == (450 << 16) | 700


def test_offset_fallback_prefers_expected_offset() -> None:
    assert _offset_fallback(300, 120) == 120
    # Clamped into the frame
    assert _offset_fallback(100, 500) == 99
    assert _offset_fallback(100, 0) == 1
    # No history: defaults to 80% of frame height
    assert _offset_fallback(300, None) == 240


def test_stride_notches_targets_viewport_minus_margin() -> None:
    # Uncalibrated: keep the small calibration step
    assert _stride_notches(544, None, 100, 2) == 2
    assert _stride_notches(544, 0.0, 100, 2) == 2
    assert _stride_notches(0, 78.0, 100, 2) == 2
    # Calibrated at 78 px/notch on a 544px frame: (544 - 100) / 78 -> 5 notches
    assert _stride_notches(544, 78.0, 100, 2) == 5
    # Margin floor of 8% of the frame keeps overlap on tall viewports
    assert _stride_notches(2000, 100.0, 100, 2) == 18
    # Frame shorter than the margin: fall back to the small step
    assert _stride_notches(80, 78.0, 100, 2) == 2
    # Tiny per-notch scrolls are capped to a sane burst size
    assert _stride_notches(544, 0.5, 100, 2) == 200


def _checkerboard(size: int, cell: int = 3) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    black = QColor("black").rgb()
    white = QColor("white").rgb()
    for y in range(size):
        for x in range(size):
            image.setPixel(x, y, black if (x // cell + y // cell) % 2 else white)
    return image


def test_find_vertical_offset_uses_history_when_match_is_bad() -> None:
    # A blank frame against a checkerboard cannot be aligned by content,
    # so the offset measured on previous frames should win over the
    # fabricated 80%-of-height default.
    img1 = QImage(300, 300, QImage.Format.Format_ARGB32)
    img1.fill(QColor("white").rgb())
    img2 = _checkerboard(300)

    assert find_vertical_offset(img1, img2, expected_offset=120) == 120
    assert find_vertical_offset(img1, img2) == 240


def test_stitch_images() -> None:
    img1 = QImage(100, 100, QImage.Format.Format_ARGB32)
    img1.fill(QColor("white").rgb())

    img2 = QImage(100, 100, QImage.Format.Format_ARGB32)
    img2.fill(QColor("black").rgb())

    stitched = stitch_images([img1, img2], [60])
    assert stitched is not None
    # Height should be 100 + 60 = 160
    assert stitched.height() == 160
    assert stitched.width() == 100
    assert stitched.pixelColor(50, 80) == QColor("white")
    assert stitched.pixelColor(50, 120) == QColor("black")

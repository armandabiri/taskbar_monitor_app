from PyQt6.QtGui import QColor, QImage

from services.screenshot_service import (
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

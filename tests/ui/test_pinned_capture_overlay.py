"""Smoke tests for the pinned capture overlay window."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QImage

from ui.pinned_capture_overlay import PinnedCaptureOverlay


def _build_image() -> QImage:
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.darkCyan)
    return image


def test_overlay_constructs_with_image_size(qtbot) -> None:
    image = _build_image()
    overlay = PinnedCaptureOverlay(image)
    qtbot.addWidget(overlay)

    assert overlay.image.size() == image.size()
    assert overlay.sizeHint().width() >= image.width()
    assert overlay._label.pixmap().size() == image.size()


def test_escape_closes_overlay(qtbot) -> None:
    overlay = PinnedCaptureOverlay(_build_image())
    qtbot.addWidget(overlay)
    overlay.show()
    qtbot.waitExposed(overlay)

    assert overlay.isVisible()
    qtbot.keyClick(overlay, Qt.Key.Key_Escape)
    assert overlay.isHidden() or not overlay.isVisible()


def test_set_opacity_percent(qtbot) -> None:
    overlay = PinnedCaptureOverlay(_build_image())
    qtbot.addWidget(overlay)

    # Qt stores window opacity as a byte, so allow a small quantization margin.
    overlay.set_opacity_percent(50)
    assert abs(overlay.windowOpacity() - 0.5) < 0.01

    overlay.set_opacity_percent(150)
    assert abs(overlay.windowOpacity() - 1.0) < 0.01


def test_pin_returns_visible_instance(qtbot) -> None:
    overlay = PinnedCaptureOverlay.pin(_build_image())
    qtbot.addWidget(overlay)

    assert isinstance(overlay, PinnedCaptureOverlay)
    assert overlay.isVisible()
    overlay.close()

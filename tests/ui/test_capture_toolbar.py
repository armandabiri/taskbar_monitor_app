from __future__ import annotations

import pytest
from PyQt6.QtWidgets import QWidget

from ui.capture_toolbar import CaptureToolbar


def test_capture_toolbar_constructs(qtbot) -> None:
    toolbar = CaptureToolbar()
    qtbot.addWidget(toolbar)
    assert toolbar is not None
    assert toolbar.region_btn is not None


@pytest.mark.parametrize(
    ("signal_name", "button_attr"),
    [
        ("region_requested", "region_btn"),
        ("element_requested", "element_btn"),
        ("full_screen_requested", "full_screen_btn"),
        ("scrolling_requested", "scrolling_btn"),
        ("settings_requested", "settings_btn"),
    ],
)
def test_button_emits_signal(qtbot, signal_name: str, button_attr: str) -> None:
    toolbar = CaptureToolbar()
    qtbot.addWidget(toolbar)

    fired = []
    getattr(toolbar, signal_name).connect(lambda: fired.append(True))
    getattr(toolbar, button_attr).click()

    assert fired == [True]


def test_dock_near_smoke(qtbot) -> None:
    toolbar = CaptureToolbar()
    qtbot.addWidget(toolbar)

    anchor = QWidget()
    qtbot.addWidget(anchor)
    anchor.setGeometry(100, 200, 300, 40)

    toolbar.dock_near(anchor)

    assert toolbar.x() >= 0
    assert toolbar.y() >= anchor.geometry().y()

"""Tests for the delayed capture countdown overlay."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeyEvent

from src.ui.capture_delay_overlay import CaptureDelayOverlay, run_with_delay


def test_construction_smoke(qtbot):
    overlay = CaptureDelayOverlay()
    qtbot.addWidget(overlay)
    assert overlay is not None


def test_zero_seconds_calls_immediately(qtbot):
    overlay = CaptureDelayOverlay()
    qtbot.addWidget(overlay)
    done = []
    overlay.start(0, lambda: done.append(True))
    assert done == [True]
    assert not overlay.isVisible()


def test_run_with_delay_zero_seconds(qtbot):
    done = []
    result = run_with_delay(0, lambda: done.append(True))
    assert result is None
    assert done == [True]


def test_countdown_fires_callback(qtbot):
    overlay = CaptureDelayOverlay()
    qtbot.addWidget(overlay)
    done = [False]
    overlay.start(1, lambda: done.__setitem__(0, True))
    assert overlay._label.text() == "1"
    qtbot.waitUntil(lambda: done[0], timeout=3000)
    assert done[0] is True


def test_label_changes_during_countdown(qtbot):
    overlay = CaptureDelayOverlay()
    qtbot.addWidget(overlay)
    done = [False]
    overlay.start(2, lambda: done.__setitem__(0, True))
    assert overlay._label.text() == "2"
    qtbot.waitUntil(lambda: overlay._label.text() == "1", timeout=2000)
    qtbot.waitUntil(lambda: done[0], timeout=3000)
    assert done[0] is True


def test_cancel_does_not_call_callback(qtbot):
    overlay = CaptureDelayOverlay()
    qtbot.addWidget(overlay)
    done = []
    overlay.start(3, lambda: done.append(True))
    overlay.cancel()
    qtbot.wait(300)
    assert done == []


def test_escape_cancels_without_callback(qtbot):
    overlay = CaptureDelayOverlay()
    qtbot.addWidget(overlay)
    done = []
    overlay.start(3, lambda: done.append(True))
    event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier,
    )
    overlay.keyPressEvent(event)
    qtbot.wait(300)
    assert done == []
    assert not overlay.isVisible()

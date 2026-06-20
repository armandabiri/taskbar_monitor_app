"""On-screen delay countdown overlay used to wrap all capture modes.

Provides a reusable scheduler that shows a centered 3-2-1 countdown and then
invokes a callback exactly once. Pure PyQt6 (no Win32/ctypes) so it imports on
headless Linux CI.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QCursor, QGuiApplication, QKeyEvent
from PyQt6.QtWidgets import QLabel, QVBoxLayout, QWidget

OnDone = Callable[[], None]


class CaptureDelayOverlay(QWidget):
    """Frameless, always-on-top centered countdown number.

    Use :meth:`start` to count down ``seconds`` and call ``on_done`` once when
    it naturally reaches zero. Pressing Esc cancels without calling ``on_done``.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self._remaining = 0
        self._on_done: OnDone | None = None
        self._fired = False

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 24, 40, 24)
        self._label = QLabel("", self)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._label.setStyleSheet(
            "color: #ffffff; font-size: 96px; font-weight: bold; "
            "background-color: rgba(20, 20, 20, 160); "
            "border-radius: 24px; padding: 16px 40px;"
        )
        layout.addWidget(self._label)

    def start(self, seconds: int, on_done: OnDone) -> None:
        """Begin the countdown.

        If ``seconds`` <= 0 the overlay is never shown and ``on_done`` is called
        immediately. Otherwise it ticks down once per second and calls
        ``on_done`` exactly once when it reaches zero.
        """
        self._fired = False
        if seconds <= 0:
            self._on_done = None
            self._invoke_done(on_done)
            return
        self._on_done = on_done
        self._remaining = int(seconds)
        self._update_label()
        self.adjustSize()
        self.show()
        self.raise_()
        self._timer.start()

    def cancel(self) -> None:
        """Cancel the countdown without calling the callback."""
        self._timer.stop()
        self._on_done = None
        self.hide()
        self.close()

    def _tick(self) -> None:
        self._remaining -= 1
        if self._remaining <= 0:
            self._finish()
            return
        self._update_label()

    def _finish(self) -> None:
        self._timer.stop()
        on_done = self._on_done
        self._on_done = None
        self.hide()
        self.close()
        if on_done is not None:
            self._invoke_done(on_done)

    def _invoke_done(self, on_done: OnDone) -> None:
        if self._fired:
            return
        self._fired = True
        on_done()

    def _update_label(self) -> None:
        self._label.setText(str(self._remaining))

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 (Qt override)
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()
            event.accept()
            return
        super().keyPressEvent(event)


def _center_on_cursor(widget: QWidget) -> None:
    screen = QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()
    if screen is None:
        return
    geo = screen.availableGeometry()
    rect = widget.frameGeometry()
    rect.moveCenter(geo.center())
    widget.move(rect.topLeft())


def run_with_delay(
    seconds: int,
    on_done: OnDone,
    parent: QWidget | None = None,
) -> CaptureDelayOverlay | None:
    """Create, position, and start a :class:`CaptureDelayOverlay`.

    Returns the overlay instance so the caller can keep a reference alive. If
    ``seconds`` <= 0 the callback fires immediately and ``None`` is returned.
    """
    if seconds <= 0:
        on_done()
        return None
    overlay = CaptureDelayOverlay(parent)
    overlay.adjustSize()
    _center_on_cursor(overlay)
    overlay.start(seconds, on_done)
    _center_on_cursor(overlay)
    return overlay

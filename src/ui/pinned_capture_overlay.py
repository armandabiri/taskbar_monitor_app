"""Frameless, always-on-top overlay that pins a captured image to the screen.

The overlay shows a :class:`PyQt6.QtGui.QImage` at its native pixel size in a
borderless tool window. The user can drag it around, adjust its opacity, copy the
image to the clipboard, and dismiss it with ``Esc`` or a context-menu action.

This module is intentionally free of Win32/ctypes usage so it can be imported on
headless Linux CI under the offscreen Qt platform plugin.
"""

from __future__ import annotations

from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtGui import QImage, QMouseEvent, QPixmap
from PyQt6.QtWidgets import QApplication, QLabel, QMenu, QVBoxLayout, QWidget

_OPACITY_CHOICES: tuple[int, ...] = (25, 50, 75, 100)


class PinnedCaptureOverlay(QWidget):
    """A draggable, frameless window that pins a captured image on screen."""

    def __init__(self, image: QImage, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._image = image
        self._drag_offset: QPoint | None = None

        self.setWindowFlags(
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setWindowTitle("Pinned Capture")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._label = QLabel(self)
        self._label.setPixmap(QPixmap.fromImage(image))
        self._label.setFixedSize(image.size())
        layout.addWidget(self._label)

        self.setFixedSize(image.size())

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------
    @classmethod
    def pin(
        cls, image: QImage, parent: QWidget | None = None
    ) -> "PinnedCaptureOverlay":
        """Create, position, show, and return a pinned overlay.

        The caller is responsible for keeping the returned reference alive so the
        window is not garbage collected while it is on screen.
        """
        overlay = cls(image, parent)
        overlay._position_near_cursor()
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        return overlay

    def _position_near_cursor(self) -> None:
        """Place the overlay near the cursor, falling back to screen center."""
        screen = QApplication.screenAt(self._cursor_pos()) or QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None

        target = self._cursor_pos()
        if available is not None and not available.contains(target):
            target = available.center()

        x, y = target.x(), target.y()
        if available is not None:
            max_x = available.right() - self.width()
            max_y = available.bottom() - self.height()
            x = min(max(x, available.left()), max(max_x, available.left()))
            y = min(max(y, available.top()), max(max_y, available.top()))
        self.move(x, y)

    @staticmethod
    def _cursor_pos() -> QPoint:
        from PyQt6.QtGui import QCursor

        return QCursor.pos()

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------
    @property
    def image(self) -> QImage:
        """The pinned image."""
        return self._image

    # ------------------------------------------------------------------
    # Dragging
    # ------------------------------------------------------------------
    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = (
                event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            )
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._drag_offset is not None and (
            event.buttons() & Qt.MouseButton.LeftButton
        ):
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    # ------------------------------------------------------------------
    # Keyboard
    # ------------------------------------------------------------------
    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------
    def contextMenuEvent(self, event) -> None:  # noqa: N802
        menu = QMenu(self)

        opacity_menu = menu.addMenu("Opacity")
        current = round(self.windowOpacity() * 100)
        for percent in _OPACITY_CHOICES:
            action = opacity_menu.addAction(f"{percent}%")
            action.setCheckable(True)
            action.setChecked(percent == current)
            action.triggered.connect(
                lambda _checked=False, p=percent: self.set_opacity_percent(p)
            )

        menu.addSeparator()
        copy_action = menu.addAction("Copy")
        copy_action.triggered.connect(self.copy_to_clipboard)

        close_action = menu.addAction("Close")
        close_action.triggered.connect(self.close)

        menu.exec(event.globalPos())
        event.accept()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def set_opacity_percent(self, percent: int) -> None:
        """Set the window opacity from a 0-100 percentage."""
        clamped = max(0, min(100, int(percent)))
        self.setWindowOpacity(clamped / 100.0)

    def copy_to_clipboard(self) -> None:
        """Copy the pinned image to the system clipboard."""
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setImage(self._image)

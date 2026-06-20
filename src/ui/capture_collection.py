"""Multi-image capture stack: collect captures, then paste them in sequence."""

from __future__ import annotations

from PyQt6.QtCore import QObject, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QLabel, QVBoxLayout, QWidget

from core.config import APP_NAME
from services.notification_service import NotificationService
from services.screenshot.key_input import send_ctrl_v


class CaptureCollection(QObject):
    """Holds a session's collected capture images.

    A session is toggled on (clearing any previous stack), captures are appended
    while it is active, and the stack survives being toggled off so it can be
    pasted afterwards.
    """

    changed = pyqtSignal(bool, int)  # active, count

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._active = False
        self._images: list[QImage] = []

    @property
    def active(self) -> bool:
        return self._active

    @property
    def count(self) -> int:
        return len(self._images)

    @property
    def images(self) -> list[QImage]:
        return list(self._images)

    def toggle(self) -> bool:
        if self._active:
            self.stop()
        else:
            self.start()
        return self._active

    def start(self) -> None:
        self._active = True
        self._images.clear()
        self.changed.emit(True, 0)

    def stop(self) -> None:
        self._active = False
        self.changed.emit(False, len(self._images))

    def add(self, image: QImage) -> None:
        if image is None or image.isNull():
            return
        self._images.append(QImage(image))
        self.changed.emit(self._active, len(self._images))

    def clear(self) -> None:
        self._images.clear()
        self.changed.emit(self._active, 0)


class CollectionBadge(QWidget):
    """Small always-on-top badge showing the live collected-image count."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setStyleSheet(
            "background-color: rgba(20, 20, 20, 220); color: #55efc4; "
            "border: 1px solid #444; border-radius: 6px; padding: 6px 10px;"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        self._label = QLabel("\U0001f4f8 Collecting: 0")
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._label)
        self._anchor: QWidget | None = None
        self.adjustSize()

    def bind(self, collection: CaptureCollection, anchor: QWidget) -> None:
        self._anchor = anchor
        collection.changed.connect(self._on_changed)

    def _on_changed(self, active: bool, count: int) -> None:
        if not active:
            self.hide()
            return
        self._label.setText(f"\U0001f4f8 Collecting: {count}  (Shift+Win+V to paste)")
        self.adjustSize()
        if self._anchor is not None:
            geom = self._anchor.geometry()
            self.move(geom.x() + 8, geom.y() + geom.height() + 8)
        self.show()
        self.raise_()


class SequentialImagePaster(QObject):
    """Paste a list of images one after another into the focused window."""

    finished = pyqtSignal(int)

    def __init__(self, parent: QObject | None = None, gap_ms: int = 320) -> None:
        super().__init__(parent)
        self._clipboard = None
        self._queue: list[QImage] = []
        self._pasted = 0
        self._gap_ms = gap_ms
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._step)

    def paste(self, images: list[QImage], clipboard) -> bool:
        if self._timer.isActive() or not images:
            return False
        self._clipboard = clipboard
        self._queue = list(images)
        self._pasted = 0
        self._timer.start(150)
        return True

    def _step(self) -> None:
        if not self._queue:
            self.finished.emit(self._pasted)
            return
        image = self._queue.pop(0)
        if self._clipboard is not None:
            self._clipboard.setImage(image)
        QApplication.processEvents()
        if send_ctrl_v():
            self._pasted += 1
        self._timer.start(self._gap_ms)


class CaptureCollectionMixin:
    """Collection toggle/paste entry points for the capture controller.

    Expects ``self._collection`` (CaptureCollection), ``self._paster``
    (SequentialImagePaster), and ``self._monitor`` to be set by the host.
    """

    def toggle_capture_collection(self) -> None:
        """Start or stop a multi-capture collection session (Shift+Win+M)."""
        if self._collection.toggle():
            NotificationService.notify(
                APP_NAME,
                "Capture collection started — capture images, then Shift+Win+V to paste them all.",
            )
            return
        count = self._collection.count
        message = (
            f"Collected {count} image(s). Press Shift+Win+V to paste them."
            if count
            else "Capture collection stopped."
        )
        NotificationService.notify(APP_NAME, message)

    def paste_capture_collection(self) -> None:
        """Paste every collected image sequentially into the focused app (Shift+Win+V)."""
        images = self._collection.images
        if not images:
            NotificationService.notify(
                APP_NAME,
                "No collected images. Start a session with Shift+Win+M.",
            )
            return
        self._paster.paste(images, self._monitor.clipboard)

    def _on_paste_finished(self, pasted: int) -> None:
        if self._collection.active:
            self._collection.stop()
        self._collection.clear()
        NotificationService.notify(APP_NAME, f"Pasted {pasted} captured image(s).")

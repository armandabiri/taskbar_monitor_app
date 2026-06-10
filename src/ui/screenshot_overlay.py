"""Custom screenshot capture overlays."""

# ruff: noqa: N802

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QPoint, QRect, Qt
from PyQt6.QtGui import QColor, QPainter, QPen, QPixmap, QScreen
from PyQt6.QtWidgets import QWidget


class _ScreenOverlay(QWidget):
    """Base full-screen overlay pinned to one Qt screen."""

    def __init__(self, screen: QScreen, on_cancelled: Callable[[], None]) -> None:
        super().__init__(None)
        self.target_screen = screen
        self.on_cancelled = on_cancelled

        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setGeometry(screen.geometry())

        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(screen)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None:
            handle.setScreen(self.target_screen)
        self.raise_()
        self.activateWindow()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.on_cancelled()
            self.close()
        else:
            super().keyPressEvent(event)


class RegionSelector(_ScreenOverlay):
    """Translucent overlay that allows the user to drag a screenshot region."""

    def __init__(
        self,
        screen: QScreen,
        screen_snapshot: QPixmap,
        on_selected: Callable[[QRect, QScreen, QPixmap], None],
        on_cancelled: Callable[[], None],
    ) -> None:
        super().__init__(screen, on_cancelled)
        self.screen_snapshot = screen_snapshot
        self.on_selected = on_selected
        self.setCursor(Qt.CursorShape.CrossCursor)

        self.start_pos: QPoint | None = None
        self.end_pos: QPoint | None = None
        self.is_selecting = False

    def paintEvent(self, event) -> None:
        """Draw the frozen screen, dim it, and keep the selected region clear."""
        del event
        painter = QPainter(self)
        if not self.screen_snapshot.isNull():
            painter.drawPixmap(self.rect(), self.screen_snapshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 95))

        if self.start_pos is None or self.end_pos is None:
            return

        rect = QRect(self.start_pos, self.end_pos).normalized().intersected(self.rect())
        if rect.isEmpty():
            return

        if not self.screen_snapshot.isNull():
            painter.drawPixmap(rect, self.screen_snapshot, self._snapshot_source_rect(rect))

        painter.setPen(QPen(QColor("#4db8ff"), 2, Qt.PenStyle.SolidLine))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        self._draw_size_label(painter, rect)

    def _snapshot_source_rect(self, rect: QRect) -> QRect:
        if self.width() <= 0 or self.height() <= 0:
            return QRect()
        scale_x = self.screen_snapshot.width() / self.width()
        scale_y = self.screen_snapshot.height() / self.height()
        return QRect(
            round(rect.x() * scale_x),
            round(rect.y() * scale_y),
            max(1, round(rect.width() * scale_x)),
            max(1, round(rect.height() * scale_y)),
        ).intersected(self.screen_snapshot.rect())

    def _draw_size_label(self, painter: QPainter, rect: QRect) -> None:
        size_text = f"{rect.width()} x {rect.height()}"
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)

        metrics = painter.fontMetrics()
        label_width = metrics.horizontalAdvance(size_text) + 10
        label_height = metrics.height() + 4

        label_x = max(6, min(rect.left(), self.width() - label_width - 6))
        label_y = rect.top() - label_height - 8
        if label_y < 6:
            label_y = min(self.height() - label_height - 6, rect.bottom() + 8)

        bg_rect = QRect(label_x, label_y, label_width, label_height)
        painter.fillRect(bg_rect, QColor(26, 26, 26, 225))
        painter.setPen(QColor("white"))
        painter.drawText(bg_rect, Qt.AlignmentFlag.AlignCenter, size_text)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.position().toPoint()
            self.end_pos = self.start_pos
            self.is_selecting = True
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.on_cancelled()
            self.close()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self.is_selecting and self.start_pos is not None:
            self.end_pos = event.position().toPoint()
            self.update()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.is_selecting:
            super().mouseReleaseEvent(event)
            return

        self.end_pos = event.position().toPoint()
        self.is_selecting = False
        self.hide()

        if self.start_pos is None or self.end_pos is None:
            self.on_cancelled()
            self.close()
            return

        rect = QRect(self.start_pos, self.end_pos).normalized().intersected(self.rect())
        if rect.width() > 5 and rect.height() > 5:
            self.on_selected(QRect(rect), self.target_screen, self.screen_snapshot)
        else:
            self.on_cancelled()
        self.close()


class ScrollSelector(_ScreenOverlay):
    """Overlay to let the user click a window for scrolling screenshot capture."""

    def __init__(
        self,
        screen: QScreen,
        on_selected: Callable[[QPoint], None],
        on_cancelled: Callable[[], None],
    ) -> None:
        super().__init__(screen, on_cancelled)
        self.on_selected = on_selected
        self.setCursor(Qt.CursorShape.SizeVerCursor)

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 70))

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            pos = event.globalPosition().toPoint()
            self.hide()
            self.on_selected(pos)
            self.close()
        elif event.button() == Qt.MouseButton.RightButton:
            self.on_cancelled()
            self.close()
        else:
            super().mousePressEvent(event)

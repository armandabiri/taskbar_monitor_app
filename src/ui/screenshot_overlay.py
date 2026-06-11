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


class ElementSelector(_ScreenOverlay):
    """Snipping-Tool-style overlay that highlights the UI element under the cursor.

    Element rectangles are resolved up front (via UI Automation) and passed in,
    so hover hit-testing is local and instant. Move to highlight the smallest
    element under the cursor, scroll the wheel to grow/shrink the selection
    through the stack of elements at that point, click to capture it, or drag to
    fall back to a free-hand rectangle. With no element data (UIA unavailable)
    it degrades to a plain region selector.
    """

    _DRAG_THRESHOLD = 6

    def __init__(
        self,
        screen: QScreen,
        screen_snapshot: QPixmap,
        element_rects: list[tuple[QRect, str]],
        on_selected: Callable[[QRect, QScreen, QPixmap], None],
        on_cancelled: Callable[[], None],
    ) -> None:
        super().__init__(screen, on_cancelled)
        self.screen_snapshot = screen_snapshot
        self.element_rects = element_rects
        self.on_selected = on_selected
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

        self.hover_rect: QRect | None = None
        self.hover_label = ""
        self._candidates: list[int] = []
        self._cycle = 0

        self.start_pos: QPoint | None = None
        self.end_pos: QPoint | None = None
        self.is_selecting = False
        self.is_dragging = False

    def _hit_candidates(self, point: QPoint) -> list[int]:
        hits = [
            index
            for index, (rect, _label) in enumerate(self.element_rects)
            if rect.contains(point)
        ]
        hits.sort(
            key=lambda index: self.element_rects[index][0].width()
            * self.element_rects[index][0].height()
        )
        return hits

    def _update_hover(self, point: QPoint) -> None:
        self._candidates = self._hit_candidates(point)
        self._cycle = 0
        self._apply_hover()

    def _apply_hover(self) -> None:
        if not self._candidates:
            self.hover_rect = None
            self.hover_label = ""
        else:
            index = self._candidates[self._cycle % len(self._candidates)]
            rect, label = self.element_rects[index]
            self.hover_rect = QRect(rect)
            self.hover_label = label
        self.update()

    def _snapshot_source_rect(self, rect: QRect) -> QRect:
        if self.width() <= 0 or self.height() <= 0 or self.screen_snapshot.isNull():
            return QRect()
        scale_x = self.screen_snapshot.width() / self.width()
        scale_y = self.screen_snapshot.height() / self.height()
        return QRect(
            round(rect.x() * scale_x),
            round(rect.y() * scale_y),
            max(1, round(rect.width() * scale_x)),
            max(1, round(rect.height() * scale_y)),
        ).intersected(self.screen_snapshot.rect())

    def _active_rect(self) -> QRect | None:
        if self.is_dragging and self.start_pos is not None and self.end_pos is not None:
            rect = QRect(self.start_pos, self.end_pos).normalized().intersected(self.rect())
            return rect if not rect.isEmpty() else None
        return self.hover_rect

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        if not self.screen_snapshot.isNull():
            painter.drawPixmap(self.rect(), self.screen_snapshot)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 110))

        rect = self._active_rect()
        if rect is None or rect.isEmpty():
            return

        if not self.screen_snapshot.isNull():
            painter.drawPixmap(rect, self.screen_snapshot, self._snapshot_source_rect(rect))
        painter.setPen(QPen(QColor("#4db8ff"), 2, Qt.PenStyle.SolidLine))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))
        self._draw_label(painter, rect)

    def _draw_label(self, painter: QPainter, rect: QRect) -> None:
        size_text = f"{rect.width()} x {rect.height()}"
        if self.hover_label and not self.is_dragging:
            size_text = f"{self.hover_label}  ·  {size_text}"
        font = painter.font()
        font.setPointSize(9)
        font.setBold(True)
        painter.setFont(font)

        metrics = painter.fontMetrics()
        label_width = min(self.width() - 12, metrics.horizontalAdvance(size_text) + 10)
        label_height = metrics.height() + 4
        label_x = max(6, min(rect.left(), self.width() - label_width - 6))
        label_y = rect.top() - label_height - 8
        if label_y < 6:
            label_y = min(self.height() - label_height - 6, rect.bottom() + 8)

        bg_rect = QRect(label_x, label_y, label_width, label_height)
        painter.fillRect(bg_rect, QColor(26, 26, 26, 225))
        painter.setPen(QColor("white"))
        painter.drawText(
            bg_rect.adjusted(5, 0, -5, 0),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            metrics.elidedText(size_text, Qt.TextElideMode.ElideRight, label_width - 10),
        )

    def wheelEvent(self, event) -> None:
        if self.is_dragging or len(self._candidates) <= 1:
            super().wheelEvent(event)
            return
        step = 1 if event.angleDelta().y() > 0 else -1
        self._cycle = (self._cycle + step) % len(self._candidates)
        self._apply_hover()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.start_pos = event.position().toPoint()
            self.end_pos = self.start_pos
            self.is_selecting = True
            self.is_dragging = False
        elif event.button() == Qt.MouseButton.RightButton:
            self.on_cancelled()
            self.close()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        point = event.position().toPoint()
        if self.is_selecting and self.start_pos is not None:
            self.end_pos = point
            if (point - self.start_pos).manhattanLength() > self._DRAG_THRESHOLD:
                self.is_dragging = True
            self.update()
        else:
            self._update_hover(point)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.is_selecting:
            super().mouseReleaseEvent(event)
            return

        self.is_selecting = False
        self.end_pos = event.position().toPoint()
        rect = self._active_rect()
        self.is_dragging = False
        self.hide()

        if rect is not None and rect.width() > 4 and rect.height() > 4:
            self.on_selected(QRect(rect), self.target_screen, self.screen_snapshot)
        else:
            self.on_cancelled()
        self.close()


class ScrollSelector(_ScreenOverlay):
    """Overlay to let the user click a window for scrolling screenshot capture."""

    def __init__(
        self,
        screen: QScreen,
        on_selected: Callable[[QPoint, QRect | None, QScreen], None],
        on_cancelled: Callable[[], None],
    ) -> None:
        super().__init__(screen, on_cancelled)
        self.on_selected = on_selected
        self.setCursor(Qt.CursorShape.SizeVerCursor)
        self.start_pos: QPoint | None = None
        self.end_pos: QPoint | None = None
        self.is_selecting = False

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 70))

        if self.start_pos is None or self.end_pos is None:
            return

        rect = QRect(self.start_pos, self.end_pos).normalized().intersected(self.rect())
        if rect.width() <= 5 or rect.height() <= 5:
            return

        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
        painter.fillRect(rect, QColor(0, 0, 0, 0))
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setPen(QPen(QColor("#4db8ff"), 2, Qt.PenStyle.SolidLine))
        painter.drawRect(rect.adjusted(0, 0, -1, -1))

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
        if rect.width() > 20 and rect.height() > 20:
            global_pos = self.mapToGlobal(rect.center())
            self.on_selected(global_pos, QRect(rect), self.target_screen)
        else:
            self.on_selected(event.globalPosition().toPoint(), None, self.target_screen)
        self.close()

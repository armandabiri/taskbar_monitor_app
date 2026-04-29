"""Resource monitoring widgets (CPU grid and oscilloscope scopes)."""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPaintEvent,
    QResizeEvent,
)
from PyQt6.QtWidgets import QWidget
from core.config import (
    CPU_CELL_SIZE,
    CPU_CELL_SPACING,
    CPU_GRID_ROWS,
    PERCENT_MAX,
    SCOPE_MIN_WIDTH,
    SCOPE_MIN_HEIGHT,
    SCOPE_HISTORY_SIZE,
    SCOPE_POINT_STEP,
    SCOPE_BOTTOM_PADDING,
    SCOPE_VERTICAL_PADDING,
    SCOPE_GRID_X_STEP,
    SCOPE_GRID_Y_STEP,
    SCOPE_LINE_WIDTH,
    SCOPE_LABEL_FONT,
    SCOPE_LABEL_FONT_SIZE,
    SCOPE_TEXT_SHADOW_ALPHA,
    SCOPE_GRID_ALPHA,
    MIN_AUTOSCALE,
)
from core.theme import ThemeEngine


class CPUBarWidget(QWidget):
    """Grid of squares representing CPU cores."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize the CPU core usage grid widget."""
        super().__init__(parent)
        self.cpu_usages: list[float] = []
        self.cell_size = CPU_CELL_SIZE
        self.spacing = CPU_CELL_SPACING
        self.rows = CPU_GRID_ROWS
        self.setFixedHeight(self.rows * (self.cell_size + self.spacing))

    def update_usage(self, cores: list[float]) -> None:
        """Update core usage values and trigger a repaint."""
        self.cpu_usages = cores
        num_cols = (len(cores) + self.rows - 1) // self.rows
        self.setFixedWidth(num_cols * (self.cell_size + self.spacing))
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:  # pylint: disable=invalid-name
        """Render CPU core squares and usage color overlays."""
        del a0
        painter = QPainter(self)
        for index, usage in enumerate(self.cpu_usages):
            x_pos = (index // self.rows) * (self.cell_size + self.spacing)
            y_pos = (index % self.rows) * (self.cell_size + self.spacing)

            ratio = usage / PERCENT_MAX
            color = QColor(int(45 + 186 * ratio), int(133 - 57 * ratio), int(219 - 159 * ratio))

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(40, 40, 40))
            painter.drawRect(x_pos, y_pos, self.cell_size, self.cell_size)

            if usage > 0:
                painter.setBrush(color)
                painter.drawRect(x_pos, y_pos, self.cell_size, self.cell_size)

            painter.setPen(QColor(255, 255, 255, 30))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(x_pos, y_pos, self.cell_size - 1, self.cell_size - 1)


class ScopeWidget(QWidget):
    """Oscilloscope style monitor for a single metric."""

    def __init__(self, label: str, color: str, parent: QWidget | None = None) -> None:
        """Initialize a scope widget with label and line color."""
        super().__init__(parent)
        self.setMinimumSize(SCOPE_MIN_WIDTH, SCOPE_MIN_HEIGHT)
        self.history: list[float] = [0.0] * SCOPE_HISTORY_SIZE
        self.secondary_history: list[float | None] = [None] * SCOPE_HISTORY_SIZE
        self.label = label
        self.color = color
        self.display_text = ""
        self.top_right_text = ""
        self.max_val_in_history = PERCENT_MAX
        self.cached_path = QPainterPath()
        self.cached_secondary_path = QPainterPath()
        self.grid_pixmap: QPixmap | None = None
        self.sec_min = 110.0
        self.sec_max = 150.0

    def update_value(self, value: float, text: str, auto_scale: bool = False, top_right_text: str = "", secondary_value: float | None = None) -> None:
        """Append a sample and rebuild the plotted path."""
        self.history.pop(0)
        self.history.append(value)
        self.secondary_history.pop(0)
        self.secondary_history.append(secondary_value)
        self.display_text = text
        self.top_right_text = top_right_text
        if auto_scale:
            self.max_val_in_history = max(max(self.history), MIN_AUTOSCALE)

        width = self.width()
        height = self.height()
        path = QPainterPath()
        secondary_path = QPainterPath()
        
        num_samples = min(len(self.history), width // SCOPE_POINT_STEP)
        visible = self.history[-num_samples:]
        visible_sec = self.secondary_history[-num_samples:]
        
        sec_started = False
        for index, sample in enumerate(visible):
            x_pos = width - (len(visible) - index) * SCOPE_POINT_STEP
            y_pos = height - SCOPE_BOTTOM_PADDING - (
                sample / self.max_val_in_history * (height - SCOPE_VERTICAL_PADDING)
            )
            if index == 0:
                path.moveTo(x_pos, y_pos)
            else:
                path.lineTo(x_pos, y_pos)
                
            sec_sample = visible_sec[index]
            if sec_sample is not None:
                # Map temperature to the Y axis dynamically based on sec_min/sec_max
                # Extend the viewable area by a bit (e.g., -30, +50 to leave some padding)
                view_min = self.sec_min - 30.0
                view_max = self.sec_max + 50.0
                clamped_temp = max(view_min, min(view_max, sec_sample))
                sec_y = height - SCOPE_BOTTOM_PADDING - (
                    (clamped_temp - view_min) / (view_max - view_min) * (height - SCOPE_VERTICAL_PADDING)
                )
                if not sec_started:
                    secondary_path.moveTo(x_pos, sec_y)
                    sec_started = True
                else:
                    secondary_path.lineTo(x_pos, sec_y)

        self.cached_path = path
        self.cached_secondary_path = secondary_path
        self.update()

    def resizeEvent(self, a0: QResizeEvent | None) -> None:  # pylint: disable=invalid-name
        """Invalidate cached grid pixmap after resize."""
        self.grid_pixmap = None
        super().resizeEvent(a0)

    def paintEvent(self, a0: QPaintEvent | None) -> None:  # pylint: disable=invalid-name
        """Render grid, line graph, and scope text overlay."""
        del a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        width = self.width()
        height = self.height()

        if self.grid_pixmap is None or self.grid_pixmap.size() != self.size():
            self.grid_pixmap = QPixmap(self.size())
            self.grid_pixmap.fill(Qt.GlobalColor.transparent)
            grid_painter = QPainter(self.grid_pixmap)
            grid_painter.setPen(QPen(QColor(255, 255, 255, SCOPE_GRID_ALPHA), 1))
            for x_pos in range(0, width + 1, SCOPE_GRID_X_STEP):
                grid_painter.drawLine(x_pos, 0, x_pos, height)
            for y_pos in range(0, height + 1, SCOPE_GRID_Y_STEP):
                grid_painter.drawLine(0, y_pos, width, y_pos)
            grid_painter.end()
        painter.drawPixmap(0, 0, self.grid_pixmap)

        # Primary signal
        if self.label in ("CPU", "RAM"):
            line_color = ThemeEngine.get_dynamic_color(self.history[-1])
        else:
            line_color = QColor(self.color)
        painter.setPen(QPen(line_color, SCOPE_LINE_WIDTH))
        painter.drawPath(self.cached_path)
        
        # Secondary signal (temperature)
        last_sec = self.secondary_history[-1]
        if last_sec is not None:
            ratio = max(0.0, min(1.0, (last_sec - 110.0) / 40.0))
            r = int(77 + (255 - 77) * ratio)
            g = int(184 + (118 - 184) * ratio)
            b = int(255 + (117 - 255) * ratio)
            sec_color = QColor(r, g, b)
            
            pen = QPen(sec_color, 2)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawPath(self.cached_secondary_path)

        painter.setFont(QFont(SCOPE_LABEL_FONT, SCOPE_LABEL_FONT_SIZE, QFont.Weight.Bold))
        full_text = f"{self.label}: {self.display_text}"
        text_rect = self.rect().adjusted(0, 0, 0, -2)
        align = Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter

        painter.setPen(QColor(0, 0, 0, SCOPE_TEXT_SHADOW_ALPHA))
        painter.drawText(text_rect.translated(1, 1), align, full_text)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(text_rect, align, full_text)

        if self.top_right_text:
            tr_rect = self.rect().adjusted(0, 2, -2, 0)
            tr_align = Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignRight
            painter.setPen(QColor(0, 0, 0, SCOPE_TEXT_SHADOW_ALPHA))
            painter.drawText(tr_rect.translated(1, 1), tr_align, self.top_right_text)
            painter.setPen(QColor(255, 255, 255))
            painter.drawText(tr_rect, tr_align, self.top_right_text)


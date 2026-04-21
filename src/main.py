"""Taskbar system monitor widget for CPU, RAM, and network usage."""

import logging
import os
import sys
from types import ModuleType

import psutil
from PyQt6.QtCore import QPoint, QSettings, Qt, QTimer
from PyQt6.QtGui import (
    QAction,
    QColor,
    QContextMenuEvent,
    QFont,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QSlider,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

try:
    import winreg as _winreg
except ImportError:
    _winreg = None


LOGGER = logging.getLogger(__name__)
WINREG: ModuleType | None = _winreg

APP_ORG = "Intelag"
APP_NAME = "TaskbarMonitor"
AUTOSTART_NAME = "IntelagTaskbarMonitor"
RUN_KEY_PATH = r"Software\Microsoft\Windows\CurrentVersion\Run"

CPU_CELL_SIZE = 5
CPU_CELL_SPACING = 1
CPU_GRID_ROWS = 4

SCOPE_MIN_WIDTH = 70
SCOPE_MIN_HEIGHT = 24
SCOPE_HISTORY_SIZE = 120
SCOPE_POINT_STEP = 2
SCOPE_BOTTOM_PADDING = 2
SCOPE_VERTICAL_PADDING = 4
SCOPE_GRID_X_STEP = 10
SCOPE_GRID_Y_STEP = 6
SCOPE_LINE_WIDTH = 1.3
SCOPE_LABEL_FONT = "Segoe UI"
SCOPE_LABEL_FONT_SIZE = 7
SCOPE_TEXT_SHADOW_ALPHA = 180
SCOPE_GRID_ALPHA = 12

BACKGROUND_RED = 10
BACKGROUND_GREEN = 10
BACKGROUND_BLUE = 10

DEFAULT_INTERVAL_MS = 1000
DEFAULT_BG_OPACITY = 230
DEFAULT_POS = -1
DEFAULT_WIDTH = -1
DEFAULT_HEIGHT = -1
DEFAULT_FALLBACK_WIDTH = 500
DEFAULT_FALLBACK_HEIGHT = 40
DEFAULT_SCREEN_PAD = 40

MIN_WIDGET_WIDTH = 150
MIN_WIDGET_HEIGHT = 20
EDGE_MARGIN = 10

MIN_OPACITY = 50
MAX_OPACITY = 255
SLIDER_WIDTH = 120

KB = 1024
MB = KB * KB
PERCENT_MAX = 100.0
MIN_AUTOSCALE = 1.0
CPU_WARMUP_INTERVAL_SECONDS = 0.1

INTERVAL_OPTIONS: tuple[tuple[str, int], ...] = (
    ("0.1s (Ultra)", 100),
    ("0.5s (Fast)", 500),
    ("1.0s (Normal)", 1000),
    ("2.0s (Slow)", 2000),
    ("5.0s (Eco)", 5000),
)


def read_setting_int(settings: QSettings, key: str, default: int) -> int:
    """Read an integer setting safely with a default fallback."""
    value = settings.value(key, default)
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        LOGGER.warning("Invalid integer setting for key=%s, using default=%s", key, default)
        return default


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

    def paintEvent(self, _event: QPaintEvent) -> None:
        """Render CPU core squares and usage color overlays."""
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
        self.label = label
        self.color = color
        self.display_text = ""
        self.max_val_in_history = PERCENT_MAX
        self.cached_path = QPainterPath()
        self.grid_pixmap: QPixmap | None = None

    def update_value(self, value: float, text: str, auto_scale: bool = False) -> None:
        """Append a sample and rebuild the plotted path."""
        self.history.pop(0)
        self.history.append(value)
        self.display_text = text
        if auto_scale:
            self.max_val_in_history = max(max(self.history), MIN_AUTOSCALE)

        width = self.width()
        height = self.height()
        path = QPainterPath()
        num_samples = min(len(self.history), width // SCOPE_POINT_STEP)
        visible = self.history[-num_samples:]
        for index, sample in enumerate(visible):
            x_pos = width - (len(visible) - index) * SCOPE_POINT_STEP
            y_pos = height - SCOPE_BOTTOM_PADDING - (
                sample / self.max_val_in_history * (height - SCOPE_VERTICAL_PADDING)
            )
            if index == 0:
                path.moveTo(x_pos, y_pos)
            else:
                path.lineTo(x_pos, y_pos)
        self.cached_path = path
        self.update()

    def resizeEvent(self, event: QResizeEvent) -> None:
        """Invalidate cached grid pixmap after resize."""
        self.grid_pixmap = None
        super().resizeEvent(event)

    def get_dynamic_color(self, value: float) -> QColor:
        """Map utilization percent to a blue-to-red gradient."""
        ratio = min(max(value / PERCENT_MAX, 0.0), 1.0)
        red = int(45 + (231 - 45) * ratio)
        green = int(133 + (76 - 133) * ratio)
        blue = int(219 + (60 - 219) * ratio)
        return QColor(red, green, blue)

    def paintEvent(self, _event: QPaintEvent) -> None:
        """Render grid, line graph, and scope text overlay."""
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

        if self.label in ("CPU", "RAM"):
            line_color = self.get_dynamic_color(self.history[-1])
        else:
            line_color = QColor(self.color)
        painter.setPen(QPen(line_color, SCOPE_LINE_WIDTH))
        painter.drawPath(self.cached_path)

        painter.setFont(QFont(SCOPE_LABEL_FONT, SCOPE_LABEL_FONT_SIZE, QFont.Weight.Bold))
        full_text = f"{self.label}: {self.display_text}"
        text_rect = self.rect().adjusted(0, 0, 0, -2)
        align = Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter

        painter.setPen(QColor(0, 0, 0, SCOPE_TEXT_SHADOW_ALPHA))
        painter.drawText(text_rect.translated(1, 1), align, full_text)
        painter.setPen(QColor(255, 255, 255))
        painter.drawText(text_rect, align, full_text)


class TaskbarMonitor(QWidget):
    """Main always-on-top taskbar monitor window."""

    def __init__(self) -> None:
        """Initialize monitor state, UI, and update timer."""
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.settings = QSettings(APP_ORG, APP_NAME)
        self.interval = read_setting_int(self.settings, "interval", DEFAULT_INTERVAL_MS)
        self.bg_opacity = read_setting_int(self.settings, "bg_opacity", DEFAULT_BG_OPACITY)
        self.old_net = psutil.net_io_counters()

        self.m_drag = False
        self.m_resize = False
        self.m_resize_edge = ""
        self.m_drag_pos = QPoint()

        self.setup_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_stats)
        self.timer.start(self.interval)
        self.load_geometry()

    def setup_ui(self) -> None:
        """Create child widgets and layout."""
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(10, 5, 10, 5)
        self.main_layout.setSpacing(12)

        self.cpu_grid = CPUBarWidget()
        self.main_layout.addWidget(self.cpu_grid)

        self.scopes = {
            "cpu": ScopeWidget("CPU", "#4db8ff"),
            "ram": ScopeWidget("RAM", "#a29bfe"),
            "up": ScopeWidget("UP", "#ff7675"),
            "dn": ScopeWidget("DN", "#55efc4"),
        }
        for scope in self.scopes.values():
            self.main_layout.addWidget(scope, 1)

    def format_speed(self, bytes_per_second: float) -> str:
        """Format network throughput in K or M units."""
        if bytes_per_second >= MB:
            return f"{bytes_per_second / MB:.1f}M"
        return f"{bytes_per_second / KB:.0f}K"

    def update_stats(self) -> None:
        """Poll system stats and refresh monitor widgets."""
        try:
            self.raise_()
            cpu = psutil.cpu_percent()
            ram = psutil.virtual_memory().percent
            self.cpu_grid.update_usage(psutil.cpu_percent(percpu=True))
            self.scopes["cpu"].update_value(cpu, f"{int(cpu)}%")
            self.scopes["ram"].update_value(ram, f"{int(ram)}%")

            new_net = psutil.net_io_counters()
            up = float(new_net.bytes_sent - self.old_net.bytes_sent)
            down = float(new_net.bytes_recv - self.old_net.bytes_recv)
            self.old_net = new_net
            self.scopes["up"].update_value(up, self.format_speed(up), auto_scale=True)
            self.scopes["dn"].update_value(down, self.format_speed(down), auto_scale=True)
        except Exception:
            LOGGER.exception("Failed to update taskbar monitor statistics")

    def load_geometry(self) -> None:
        """Load saved position and size with safe defaults."""
        x_pos = read_setting_int(self.settings, "pos_x", DEFAULT_POS)
        y_pos = read_setting_int(self.settings, "pos_y", DEFAULT_POS)
        width = read_setting_int(self.settings, "width", DEFAULT_WIDTH)
        height = read_setting_int(self.settings, "height", DEFAULT_HEIGHT)

        if x_pos != DEFAULT_POS and y_pos != DEFAULT_POS:
            self.move(x_pos, y_pos)
        else:
            screen = QApplication.primaryScreen()
            if screen is None:
                LOGGER.warning("Primary screen unavailable, using origin fallback")
                self.move(0, 0)
            else:
                available = screen.availableGeometry()
                self.move(
                    available.width() - DEFAULT_FALLBACK_WIDTH,
                    available.height() - DEFAULT_SCREEN_PAD,
                )

        if width != DEFAULT_WIDTH and height != DEFAULT_HEIGHT:
            self.resize(width, height)

    def save_geometry(self) -> None:
        """Persist geometry and monitor settings."""
        pos = self.pos()
        self.settings.setValue("pos_x", pos.x())
        self.settings.setValue("pos_y", pos.y())
        self.settings.setValue("width", self.width())
        self.settings.setValue("height", self.height())
        self.settings.setValue("interval", self.interval)
        self.settings.setValue("bg_opacity", self.bg_opacity)
        self.settings.sync()

    def set_interval(self, milliseconds: int) -> None:
        """Set update interval and persist the new value."""
        self.interval = milliseconds
        self.timer.setInterval(self.interval)
        self.settings.setValue("interval", self.interval)
        self.settings.sync()

    def update_opacity(self, value: int) -> None:
        """Set panel opacity and persist the new value."""
        self.bg_opacity = value
        self.update()
        self.settings.setValue("bg_opacity", self.bg_opacity)
        self.settings.sync()

    def is_autostart_enabled(self) -> bool:
        """Return whether the startup Run key already exists."""
        if WINREG is None:
            return False
        try:
            with WINREG.OpenKey(
                WINREG.HKEY_CURRENT_USER,
                RUN_KEY_PATH,
                0,
                WINREG.KEY_READ,
            ) as registry_key:
                WINREG.QueryValueEx(registry_key, AUTOSTART_NAME)
            return True
        except OSError:
            return False

    def toggle_autostart(self) -> None:
        """Toggle autostart entry in the current user registry key."""
        if WINREG is None:
            LOGGER.warning("Autostart toggle unavailable: winreg not present")
            return

        if self.is_autostart_enabled():
            try:
                with WINREG.OpenKey(
                    WINREG.HKEY_CURRENT_USER,
                    RUN_KEY_PATH,
                    0,
                    WINREG.KEY_SET_VALUE,
                ) as registry_key:
                    WINREG.DeleteValue(registry_key, AUTOSTART_NAME)
            except OSError:
                LOGGER.exception("Failed to disable autostart")
        else:
            try:
                command = f'"{sys.executable}" "{os.path.abspath(sys.argv[0])}"'
                with WINREG.OpenKey(
                    WINREG.HKEY_CURRENT_USER,
                    RUN_KEY_PATH,
                    0,
                    WINREG.KEY_SET_VALUE,
                ) as registry_key:
                    WINREG.SetValueEx(registry_key, AUTOSTART_NAME, 0, WINREG.REG_SZ, command)
            except OSError:
                LOGGER.exception("Failed to enable autostart")

    def paintEvent(self, _event: QPaintEvent) -> None:
        """Paint the translucent monitor background."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor(BACKGROUND_RED, BACKGROUND_GREEN, BACKGROUND_BLUE, self.bg_opacity))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRect(self.rect())

    def get_edge(self, point: QPoint) -> str | None:
        """Return hovered resize edge descriptor when pointer is near borders."""
        width = self.width()
        height = self.height()
        edge = ""

        if point.y() < EDGE_MARGIN:
            edge = "top"
        elif point.y() > height - EDGE_MARGIN:
            edge = "bottom"

        if point.x() < EDGE_MARGIN:
            edge = "left" if not edge else f"{edge}-left"
        elif point.x() > width - EDGE_MARGIN:
            edge = "right" if not edge else f"{edge}-right"

        return edge or None

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Start drag or resize interaction on left-click."""
        if event.button() != Qt.MouseButton.LeftButton:
            return
        edge = self.get_edge(event.pos())
        if edge:
            self.m_resize = True
            self.m_resize_edge = edge
            return
        self.m_drag = True
        self.m_drag_pos = event.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Resize, drag, or update cursor based on pointer position."""
        if self.m_resize:
            rect = self.geometry()
            global_point = event.globalPosition().toPoint()
            if "right" in self.m_resize_edge:
                rect.setRight(global_point.x())
            if "bottom" in self.m_resize_edge:
                rect.setBottom(global_point.y())
            if "left" in self.m_resize_edge:
                rect.setLeft(global_point.x())
            if "top" in self.m_resize_edge:
                rect.setTop(global_point.y())
            if rect.width() > MIN_WIDGET_WIDTH and rect.height() > MIN_WIDGET_HEIGHT:
                self.setGeometry(rect)
            return

        if self.m_drag:
            self.move(event.globalPosition().toPoint() - self.m_drag_pos)
            return

        edge = self.get_edge(event.pos())
        if edge is None:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return
        if edge in ("right", "left"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edge in ("bottom", "top"):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif edge in ("bottom-right", "top-left"):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        """Stop drag or resize operation and persist geometry."""
        self.m_drag = False
        self.m_resize = False
        self.save_geometry()

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Render context menu for monitor settings and actions."""
        menu = QMenu(self)
        menu.setStyleSheet(
            """
            QMenu { background-color: #1a1a1a; color: white; border: 1px solid #333; padding: 5px; }
            QMenu::item:selected { background-color: #333; }
            QLabel { color: #aaa; font-size: 10px; padding: 0 5px; }
            """
        )

        trans_action = QWidgetAction(self)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(10, 5, 10, 5)
        label = QLabel("Background Opacity")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(MIN_OPACITY, MAX_OPACITY)
        slider.setValue(self.bg_opacity)
        slider.setFixedWidth(SLIDER_WIDTH)
        slider.valueChanged.connect(self.update_opacity)
        container_layout.addWidget(label)
        container_layout.addWidget(slider)
        trans_action.setDefaultWidget(container)
        menu.addAction(trans_action)

        menu.addSeparator()

        interval_menu = menu.addMenu("Update Interval")
        for label_text, milliseconds in INTERVAL_OPTIONS:
            action = QAction(label_text, self)
            action.setCheckable(True)
            action.setChecked(self.interval == milliseconds)
            action.triggered.connect(
                lambda _checked, ms=milliseconds: self.set_interval(ms)
            )
            interval_menu.addAction(action)

        menu.addSeparator()

        autostart_action = QAction("Auto Start with Windows", self)
        autostart_action.setCheckable(True)
        autostart_action.setChecked(self.is_autostart_enabled())
        autostart_action.triggered.connect(self.toggle_autostart)
        menu.addAction(autostart_action)

        menu.addSeparator()

        quit_action = QAction("Exit", self)
        app = QApplication.instance()
        if app is not None:
            quit_action.triggered.connect(app.quit)
        menu.addAction(quit_action)
        menu.exec(event.globalPos())


def example_usage() -> int:
    """Run the taskbar monitor application."""
    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    psutil.cpu_percent(interval=CPU_WARMUP_INTERVAL_SECONDS)
    monitor = TaskbarMonitor()
    monitor.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(example_usage())

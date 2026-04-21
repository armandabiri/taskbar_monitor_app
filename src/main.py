import ctypes
import logging
import os
import sys

import psutil
from PyQt6.QtCore import QPoint, QSettings, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (
    QContextMenuEvent,
    QIcon,
    QMouseEvent,
    QPainter,
    QPaintEvent,
)
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QPushButton,
    QWidget,
)

# Core
from core.config import (
    APP_NAME,
    APP_ORG,
    CPU_WARMUP_INTERVAL_SECONDS,
    DEFAULT_BG_OPACITY,
    DEFAULT_FALLBACK_WIDTH,
    DEFAULT_HEIGHT,
    DEFAULT_INTERVAL_MS,
    DEFAULT_POS,
    DEFAULT_SCREEN_PAD,
    DEFAULT_WIDTH,
    EDGE_MARGIN,
    KB,
    MB,
    MIN_WIDGET_HEIGHT,
    MIN_WIDGET_WIDTH,
    read_setting_int,
)
from core.theme import ThemeEngine
from services.notification_service import NotificationService

# Services
from services.resource_manager import release_resources
from services.shortcut_service import ShortcutService
# UI
from ui.widgets import CPUBarWidget, ScopeWidget
from ui.timer_widget import CountdownTimerWidget
from ui.menu_handler import AutostartManager, ContextMenuHandler
LOGGER = logging.getLogger(__name__)


def get_resource_path(relative_path: str) -> str:
    """Get absolute path to resource, works for dev and for PyInstaller."""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS  # type: ignore
    except (AttributeError, Exception):
        # We assume assets are in src/assets/ relative to this file
        base_path = os.path.dirname(__file__)
    return os.path.join(base_path, relative_path)


class TaskbarMonitor(QWidget):
    """Main always-on-top taskbar monitor window."""

    # Thread-safe signals for global shortcuts
    request_release = pyqtSignal()
    request_aggressive = pyqtSignal()

    def __init__(self) -> None:
        """Initialize monitor state, UI, and update timer."""
        super().__init__()

        # Set Application Icon
        icon_path = get_resource_path(os.path.join("assets", "taskbar-monitor.svg"))
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        self.request_release.connect(lambda: self._on_release_resources(aggressive=False))
        self.request_aggressive.connect(lambda: self._on_release_resources(aggressive=True))
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
        self._hwnd: int = 0  # Cached window handle for topmost enforcement

        self.menu_handler = ContextMenuHandler(self)
        self.setup_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_stats)
        self.timer.timeout.connect(self._enforce_topmost)
        self.timer.start(self.interval)
        self.load_geometry()

        # Global shortcuts
        self.shortcut_service = ShortcutService()
        self.shortcut_service.register_shortcuts(self)

    def _apply_win32_topmost(self) -> None:
        """Apply Win32 extended styles after the window is shown."""
        try:
            self._hwnd = int(self.winId())

            # Add WS_EX_TOOLWINDOW to hide from Alt+Tab / taskbar
            GWL_EXSTYLE = -20
            WS_EX_TOOLWINDOW = 0x00000080
            WS_EX_TOPMOST = 0x00000008
            WS_EX_NOACTIVATE = 0x08000000
            user32 = ctypes.windll.user32
            cur_style = user32.GetWindowLongW(self._hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(
                self._hwnd, GWL_EXSTYLE,
                cur_style | WS_EX_TOOLWINDOW | WS_EX_TOPMOST | WS_EX_NOACTIVATE
            )

            # Force HWND_TOPMOST position
            HWND_TOPMOST = -1
            SWP_NOMOVE = 0x0002
            SWP_NOSIZE = 0x0001
            SWP_NOACTIVATE = 0x0010
            SWP_FRAMECHANGED = 0x0020
            user32.SetWindowPos(
                self._hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED
            )
        except Exception as exc:
            LOGGER.warning("Failed to apply Win32 topmost styles: %s", exc)

    def _enforce_topmost(self) -> None:
        """Periodically re-assert topmost Z-order via Win32 API.

        Uses the 'toggle trick': briefly remove topmost, then re-add it.
        This forces Windows DWM to recalculate Z-order, reliably
        placing the window above the Windows 11 taskbar.
        """
        try:
            if not self._hwnd:
                self._hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            swp_flags = 0x0002 | 0x0001 | 0x0010  # NOMOVE | NOSIZE | NOACTIVATE

            # Step 1: Briefly remove topmost
            user32.SetWindowPos(self._hwnd, -2, 0, 0, 0, 0, swp_flags)  # HWND_NOTOPMOST

            # Step 2: Re-add topmost — forces DWM to recalculate
            user32.SetWindowPos(self._hwnd, -1, 0, 0, 0, 0, swp_flags)  # HWND_TOPMOST
        except Exception:
            pass

    def setup_ui(self) -> None:
        """Create child widgets and layout."""
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(10, 5, 10, 5)
        self.main_layout.setSpacing(12)

        # --- Left side: Resource Release buttons ---
        btn_style = """
            QPushButton {
                background-color: rgba(40, 40, 40, 200);
                color: #55efc4;
                border: 1px solid #444;
                border-radius: 4px;
                font-size: 13px;
                padding: 0;
            }
            QPushButton:hover {
                background-color: rgba(70, 70, 70, 220);
                border-color: #55efc4;
            }
            QPushButton:pressed {
                background-color: rgba(85, 239, 196, 60);
            }
            QPushButton:disabled {
                color: #fdcb6e;
                background-color: rgba(60, 60, 60, 180);
            }
        """

        # AutoSmart Button
        self.smart_btn = QPushButton("🧠", self)
        self.smart_btn.setToolTip("AutoSmart: Release memory (skips active apps) [Ctrl+Shift+Alt+Delete]")
        self.smart_btn.setFixedSize(24, 24)
        self.smart_btn.setStyleSheet(btn_style)
        self.smart_btn.clicked.connect(lambda: self._on_release_resources(aggressive=False))
        self.main_layout.addWidget(self.smart_btn)

        # Aggressive Button
        self.aggressive_btn = QPushButton("⚡", self)
        self.aggressive_btn.setToolTip("Aggressive: Deep memory cleanup (all background apps) [Ctrl+Shift+Alt+Backspace]")
        self.aggressive_btn.setFixedSize(24, 24)
        self.aggressive_btn.setStyleSheet(btn_style)
        self.aggressive_btn.clicked.connect(lambda: self._on_release_resources(aggressive=True))
        self.main_layout.addWidget(self.aggressive_btn)

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

        # --- Right side: Countdown Timer ---
        self.countdown_timer = CountdownTimerWidget(self)
        self.countdown_timer.setFixedWidth(72)
        self.main_layout.addWidget(self.countdown_timer)

    def _on_release_resources(self, aggressive: bool = False) -> None:
        """Trigger resource release and flash feedback on the calling button."""
        btn = self.aggressive_btn if aggressive else self.smart_btn
        btn.setEnabled(False)
        old_text = btn.text()
        btn.setText("⏳")
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

        try:
            result = release_resources(aggressive=aggressive)
            LOGGER.info("Resource release (%s): %s", "Aggressive" if aggressive else "AutoSmart", result.summary)
            btn.setToolTip(f"Last freed: {result.ram_freed_mb:.1f} MB")

            # Show system notification
            mode_name = "Aggressive Clear" if aggressive else "AutoSmart Clear"
            NotificationService.notify(mode_name, result.details)
        except OSError:
            LOGGER.exception("OS error during resource release")
        except Exception: # pylint: disable=broad-exception-caught
            LOGGER.exception("Resource release failed")
        finally:
            QTimer.singleShot(600, lambda: self._restore_btn(btn, old_text))

    def _restore_btn(self, btn: QPushButton, text: str) -> None:
        """Restore the release button state."""
        btn.setText(text)
        btn.setEnabled(True)

    def format_speed(self, bytes_per_second: float) -> str:
        """Format network throughput in K or M units."""
        if bytes_per_second >= MB:
            return f"{bytes_per_second / MB:.1f}M"
        return f"{bytes_per_second / KB:.0f}K"

    def update_stats(self) -> None:
        """Poll system stats and refresh monitor widgets."""
        try:
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

            # Orchestrated timer tick
            self.countdown_timer.tick()
        except (psutil.Error, RuntimeError):
            LOGGER.exception("Failed to update taskbar monitor statistics")
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Unexpected error during stats update")

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
        """Check if autostart is enabled."""
        return AutostartManager.is_enabled()

    def toggle_autostart(self) -> None:
        """Toggle autostart status."""
        AutostartManager.toggle()
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:  # pylint: disable=invalid-name
        """Paint the translucent monitor background."""
        painter = QPainter(self)
        ThemeEngine.paint_background(painter, self.rect(), self.bg_opacity)

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

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:  # pylint: disable=invalid-name
        """Handle mouse press for dragging/resizing."""
        if a0 is None or a0.button() != Qt.MouseButton.LeftButton:
            return
        edge = self.get_edge(a0.pos())
        if edge:
            self.m_resize = True
            self.m_resize_edge = edge
            return
        self.m_drag = True
        self.setCursor(Qt.CursorShape.SizeAllCursor)
        self.m_drag_pos = a0.globalPosition().toPoint() - self.pos()

    def mouseMoveEvent(self, a0: QMouseEvent | None) -> None:  # pylint: disable=invalid-name
        """Handle mouse move for dragging/resizing."""
        if a0 is None:
            return
            
        if self.m_resize:
            rect = self.geometry()
            global_point = a0.globalPosition().toPoint()
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
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            self.move(a0.globalPosition().toPoint() - self.m_drag_pos)
            return

        edge = self.get_edge(a0.pos())
        if edge is None:
            self.setCursor(Qt.CursorShape.SizeAllCursor)
            return
        if edge in ("right", "left"):
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif edge in ("bottom", "top"):
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        elif edge in ("bottom-right", "top-left"):
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        else:
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)

    def mouseReleaseEvent(self, a0: QMouseEvent | None) -> None:  # pylint: disable=invalid-name
        """Handle mouse release."""
        del a0
        self.m_drag = False
        self.m_resize = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.save_geometry()

    def contextMenuEvent(self, a0: QContextMenuEvent | None) -> None:  # pylint: disable=invalid-name
        """Handle context menu event."""
        if a0 is not None:
            self.menu_handler.handle_event(a0)

    def closeEvent(self, a0) -> None:  # pylint: disable=invalid-name
        """Cleanup shortcuts on close."""
        self.shortcut_service.unregister_all()
        super().closeEvent(a0)


def main() -> int:
    """Run the taskbar monitor application."""
    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    psutil.cpu_percent(interval=CPU_WARMUP_INTERVAL_SECONDS)
    monitor = TaskbarMonitor()
    monitor.show()

    # Apply Win32 extended styles for reliable topmost over Windows taskbar
    monitor._apply_win32_topmost()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

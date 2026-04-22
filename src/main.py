import ctypes
import logging
import os
import sys

import psutil
from PyQt6.QtCore import QEvent, QPoint, QSettings, Qt, QTimer, pyqtSignal
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
    COLOR_GPU,
    COLOR_TEMP,
    COLOR_VRAM,
    CPU_WARMUP_INTERVAL_SECONDS,
    DEFAULT_AUTOHIDE_FULLSCREEN,
    DEFAULT_BG_OPACITY,
    DEFAULT_CLICK_THROUGH,
    DEFAULT_FALLBACK_WIDTH,
    DEFAULT_HEIGHT,
    DEFAULT_INTERVAL_MS,
    DEFAULT_MINIMIZE_TO_TRAY,
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
from services.system_info import (
    foreground_is_fullscreen,
    get_battery,
    get_cpu_temp,
    get_gpu_stats,
    prime_process_cpu,
)
# UI
from ui.battery_widget import BatteryWidget
from ui.menu_handler import AutostartManager, ContextMenuHandler
from ui.process_popup import TopProcessesPopup
from ui.system_tray import build_tray
from ui.timer_widget import CountdownTimerWidget
from ui.widgets import CPUBarWidget, ScopeWidget

LOGGER = logging.getLogger(__name__)


# Win32 constants for window styles
_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_TOPMOST = 0x00000008
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020
_WM_WINDOWPOSCHANGING = 0x0046


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    """Windows MSG — only fields we need, full layout so ctypes aligns right."""
    _fields_ = [
        ("hwnd", ctypes.c_ssize_t),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_ssize_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_ulong),
        ("pt", _POINT),
        ("lPrivate", ctypes.c_ulong),
    ]


class _WINDOWPOS(ctypes.Structure):
    """Windows WINDOWPOS struct — layout must match native Win32."""
    _fields_ = [
        ("hwnd", ctypes.c_ssize_t),
        ("hwndInsertAfter", ctypes.c_ssize_t),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("cx", ctypes.c_int),
        ("cy", ctypes.c_int),
        ("flags", ctypes.c_uint),
    ]


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
    request_toggle_click_through = pyqtSignal()

    def __init__(self) -> None:
        """Initialize monitor state, UI, and update timer."""
        super().__init__()

        # Set Application Icon
        self._icon_path = get_resource_path(os.path.join("assets", "taskbar-monitor.svg"))
        if os.path.exists(self._icon_path):
            self.setWindowIcon(QIcon(self._icon_path))

        self.request_release.connect(lambda: self._on_release_resources(aggressive=False))
        self.request_aggressive.connect(lambda: self._on_release_resources(aggressive=True))
        self.request_toggle_click_through.connect(
            lambda: self.set_click_through(not self.click_through)
        )
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
        self.click_through = bool(read_setting_int(
            self.settings, "click_through", DEFAULT_CLICK_THROUGH))
        self.autohide_fullscreen = bool(read_setting_int(
            self.settings, "autohide_fullscreen", DEFAULT_AUTOHIDE_FULLSCREEN))
        self.minimize_to_tray = bool(read_setting_int(
            self.settings, "minimize_to_tray", DEFAULT_MINIMIZE_TO_TRAY))

        self.old_net = psutil.net_io_counters()
        self.old_disk = psutil.disk_io_counters()

        self.m_drag = False
        self.m_resize = False
        self.m_resize_edge = ""
        self.m_drag_pos = QPoint()
        self._hwnd: int = 0
        self._topmost_applied = False
        self._hidden_for_fullscreen = False

        self.menu_handler = ContextMenuHandler(self)

        # Probe optional capabilities before building UI
        self._gpu_available = get_gpu_stats().available
        self._battery_available = get_battery() is not None
        self._temp_available = get_cpu_temp() is not None or (
            self._gpu_available and get_gpu_stats().temp_c is not None
        )

        self.process_popup: TopProcessesPopup | None = None
        self._last_release_error_count = 0

        self.setup_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_stats)
        self.timer.timeout.connect(self._check_fullscreen_autohide)
        self.timer.start(self.interval)

        # Topmost enforcement runs on its own fast, fixed-rate timer so that
        # a slow (Eco) stats interval doesn't let the Win11 taskbar get above us.
        self.topmost_timer = QTimer(self)
        self.topmost_timer.setInterval(500)
        self.topmost_timer.timeout.connect(self._enforce_topmost)
        self.topmost_timer.start()

        self.load_geometry()

        # Global shortcuts — capture failures for user-visible warning
        self.shortcut_service = ShortcutService()
        failed_hotkeys = self.shortcut_service.register_shortcuts(self)
        if failed_hotkeys:
            NotificationService.notify(
                APP_NAME,
                f"Could not register {len(failed_hotkeys)} global hotkey(s): "
                + ", ".join(failed_hotkeys),
            )

        # System tray
        self.tray = build_tray(
            parent=self,
            icon_path=self._icon_path,
            on_toggle_visibility=self.toggle_visibility,
            on_release_smart=lambda: self._on_release_resources(aggressive=False),
            on_release_aggressive=lambda: self._on_release_resources(aggressive=True),
            on_show_processes=self.show_processes_popup,
            get_click_through=lambda: self.click_through,
            on_set_click_through=self.set_click_through,
        )

    # ------------------------------------------------------------------
    # Window topmost enforcement
    # ------------------------------------------------------------------
    def _apply_win32_topmost(self) -> None:
        """Apply Win32 extended styles. Idempotent — safe to call repeatedly."""
        try:
            self._hwnd = int(self.winId())
            user32 = ctypes.windll.user32
            cur_style = user32.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
            new_style = cur_style | _WS_EX_TOOLWINDOW | _WS_EX_TOPMOST | _WS_EX_NOACTIVATE
            if self.click_through:
                new_style |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT
            if new_style != cur_style:
                user32.SetWindowLongW(self._hwnd, _GWL_EXSTYLE, new_style)
            user32.SetWindowPos(
                self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
            )
            self._topmost_applied = True
        except OSError as exc:
            LOGGER.warning("Failed to apply Win32 topmost styles: %s", exc)

    def _enforce_topmost(self) -> None:
        """Periodically re-assert topmost Z-order via Win32 API."""
        if not self._hwnd or not self.isVisible():
            return
        try:
            user32 = ctypes.windll.user32
            swp_flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE
            # Toggle trick forces DWM to recompute Z-order above the Win11 taskbar.
            user32.SetWindowPos(self._hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0, swp_flags)
            user32.SetWindowPos(self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0, swp_flags)
        except OSError:
            pass

    def nativeEvent(self, event_type, message):  # pylint: disable=invalid-name
        """Intercept WM_WINDOWPOSCHANGING to pin ourselves at HWND_TOPMOST.

        Windows sends WM_WINDOWPOSCHANGING before every Z-order change. By
        rewriting the `hwndInsertAfter` field to HWND_TOPMOST in-place, we
        prevent the Win11 taskbar (or any other window) from ever being
        placed above us — even between ticks of the 500ms enforcement timer.
        """
        try:
            if event_type == b"windows_generic_MSG" and self._topmost_applied:
                msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
                if msg.message == _WM_WINDOWPOSCHANGING and msg.lParam:
                    wp = ctypes.cast(msg.lParam, ctypes.POINTER(_WINDOWPOS)).contents
                    if not (wp.flags & _SWP_NOZORDER):
                        wp.hwndInsertAfter = _HWND_TOPMOST  # -1
        except (ValueError, OSError):
            pass
        return False, 0

    def showEvent(self, a0) -> None:  # pylint: disable=invalid-name
        """Re-apply topmost every time the window becomes visible."""
        super().showEvent(a0)
        # Deferred slightly so winId() has a real HWND on first show.
        QTimer.singleShot(0, self._apply_win32_topmost)
        QTimer.singleShot(50, self._enforce_topmost)

    # ------------------------------------------------------------------
    # Click-through toggle
    # ------------------------------------------------------------------
    def set_click_through(self, enabled: bool) -> None:
        """Toggle click-through mode and persist the setting."""
        self.click_through = enabled
        self.settings.setValue("click_through", 1 if enabled else 0)
        self.settings.sync()
        if self._hwnd:
            try:
                user32 = ctypes.windll.user32
                cur = user32.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
                if enabled:
                    cur |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT
                else:
                    cur &= ~_WS_EX_TRANSPARENT  # keep LAYERED; removing it can cause flicker
                user32.SetWindowLongW(self._hwnd, _GWL_EXSTYLE, cur)
            except OSError as exc:
                LOGGER.warning("Failed to toggle click-through: %s", exc)
        NotificationService.notify(
            APP_NAME,
            "Click-through ON — window ignores mouse. Press Ctrl+Shift+Alt+C to disable."
            if enabled
            else "Click-through OFF — window accepts mouse again.",
        )

    def set_autohide_fullscreen(self, enabled: bool) -> None:
        """Toggle auto-hide on fullscreen and persist."""
        self.autohide_fullscreen = enabled
        self.settings.setValue("autohide_fullscreen", 1 if enabled else 0)
        self.settings.sync()
        if not enabled and self._hidden_for_fullscreen:
            self._hidden_for_fullscreen = False
            self.show()

    def set_minimize_to_tray(self, enabled: bool) -> None:
        """Toggle minimize-to-tray and persist."""
        self.minimize_to_tray = enabled
        self.settings.setValue("minimize_to_tray", 1 if enabled else 0)
        self.settings.sync()

    def _check_fullscreen_autohide(self) -> None:
        """Hide when a fullscreen app is in the foreground; restore otherwise."""
        if not self.autohide_fullscreen:
            return
        fullscreen = foreground_is_fullscreen()
        if fullscreen and not self._hidden_for_fullscreen and self.isVisible():
            self._hidden_for_fullscreen = True
            self.hide()
        elif not fullscreen and self._hidden_for_fullscreen:
            self._hidden_for_fullscreen = False
            self.show()

    def toggle_visibility(self) -> None:
        """Show or hide the monitor (used by tray icon)."""
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()

    def show_processes_popup(self) -> None:
        """Open (or raise) the top-processes popup."""
        if self.process_popup is None:
            self.process_popup = TopProcessesPopup()
        pos = self.pos()
        self.process_popup.move(pos.x(), max(0, pos.y() - 240))
        self.process_popup.show()
        self.process_popup.raise_()
        self.process_popup.activateWindow()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def setup_ui(self) -> None:
        """Create child widgets and layout."""
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(10, 5, 10, 5)
        self.main_layout.setSpacing(12)

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
        self.smart_btn.setToolTip(
            "AutoSmart: Release memory (skips active apps) [Ctrl+Shift+Alt+Delete]"
        )
        self.smart_btn.setFixedSize(24, 24)
        self.smart_btn.setStyleSheet(btn_style)
        self.smart_btn.clicked.connect(lambda: self._on_release_resources(aggressive=False))
        self.main_layout.addWidget(self.smart_btn)

        # Aggressive Button
        self.aggressive_btn = QPushButton("⚡", self)
        self.aggressive_btn.setToolTip(
            "Aggressive: Deep memory cleanup (all background apps) [Ctrl+Shift+Alt+Backspace]"
        )
        self.aggressive_btn.setFixedSize(24, 24)
        self.aggressive_btn.setStyleSheet(btn_style)
        self.aggressive_btn.clicked.connect(lambda: self._on_release_resources(aggressive=True))
        self.main_layout.addWidget(self.aggressive_btn)

        # Top-processes popup trigger
        self.procs_btn = QPushButton("📊", self)
        self.procs_btn.setToolTip("Show top processes")
        self.procs_btn.setFixedSize(24, 24)
        self.procs_btn.setStyleSheet(btn_style)
        self.procs_btn.clicked.connect(self.show_processes_popup)
        self.main_layout.addWidget(self.procs_btn)

        self.cpu_grid = CPUBarWidget()
        self.main_layout.addWidget(self.cpu_grid)

        self.scopes: dict[str, ScopeWidget] = {
            "cpu": ScopeWidget("CPU", "#4db8ff"),
            "ram": ScopeWidget("RAM", "#a29bfe"),
            "up": ScopeWidget("UP", "#ff7675"),
            "dn": ScopeWidget("DN", "#55efc4"),
            "r/w": ScopeWidget("R/W", "#fdcb6e"),
        }
        if self._gpu_available:
            self.scopes["gpu"] = ScopeWidget("GPU", COLOR_GPU)
            self.scopes["vram"] = ScopeWidget("VRAM", COLOR_VRAM)
        if self._temp_available:
            self.scopes["temp"] = ScopeWidget("TEMP", COLOR_TEMP)

        for scope in self.scopes.values():
            self.main_layout.addWidget(scope, 1)

        # Battery (only shown when a battery is present)
        self.battery_widget = BatteryWidget(self)
        self.battery_widget.update_stats(get_battery())
        if self._battery_available:
            self.main_layout.addWidget(self.battery_widget)
        else:
            self.battery_widget.hide()

        # Countdown timer (persists last preset)
        self.countdown_timer = CountdownTimerWidget(self, settings=self.settings)
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
            LOGGER.info(
                "Resource release (%s): %s",
                "Aggressive" if aggressive else "AutoSmart", result.summary,
            )
            mode_name = "Aggressive Clear" if aggressive else "AutoSmart Clear"
            tooltip = f"Last freed: {result.ram_freed_mb:.1f} MB"
            if result.errors:
                tooltip += f" — {len(result.errors)} error(s)"
                # Surface the first two error strings so the user knows what went wrong
                detail = "\n".join(result.errors[:2])
                NotificationService.notify(
                    f"{mode_name} (partial)",
                    f"{result.details}\n\nIssues:\n{detail}",
                )
                self._last_release_error_count = len(result.errors)
            else:
                NotificationService.notify(mode_name, result.details)
                self._last_release_error_count = 0
            btn.setToolTip(tooltip)
        except OSError:
            LOGGER.exception("OS error during resource release")
            btn.setToolTip("Release failed — see log")
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Resource release failed")
            btn.setToolTip("Release failed — see log")
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
            # Single per-CPU sample; average it for the scalar scope to avoid
            # calling cpu_percent() twice (each call resets psutil's internal
            # baseline, which distorts readings).
            per_cpu = psutil.cpu_percent(percpu=True)
            cpu = sum(per_cpu) / len(per_cpu) if per_cpu else 0.0
            ram = psutil.virtual_memory().percent
            self.cpu_grid.update_usage(per_cpu)
            self.scopes["cpu"].update_value(cpu, f"{int(cpu)}%")
            self.scopes["ram"].update_value(ram, f"{int(ram)}%")

            new_net = psutil.net_io_counters()
            up = float(new_net.bytes_sent - self.old_net.bytes_sent)
            down = float(new_net.bytes_recv - self.old_net.bytes_recv)
            self.old_net = new_net
            self.scopes["up"].update_value(up, self.format_speed(up), auto_scale=True)
            self.scopes["dn"].update_value(down, self.format_speed(down), auto_scale=True)

            new_disk = psutil.disk_io_counters()
            if new_disk and self.old_disk:
                r_diff = new_disk.read_bytes - self.old_disk.read_bytes
                w_diff = new_disk.write_bytes - self.old_disk.write_bytes
                disk_rw = float(r_diff + w_diff)
            else:
                disk_rw = 0.0
            self.old_disk = new_disk
            self.scopes["r/w"].update_value(disk_rw, self.format_speed(disk_rw), auto_scale=True)

            if "gpu" in self.scopes or "vram" in self.scopes or "temp" in self.scopes:
                gpu = get_gpu_stats()
                if "gpu" in self.scopes and gpu.util_percent is not None:
                    self.scopes["gpu"].update_value(gpu.util_percent, f"{int(gpu.util_percent)}%")
                if "vram" in self.scopes and gpu.vram_percent is not None:
                    vram_pct = gpu.vram_percent
                    self.scopes["vram"].update_value(vram_pct, f"{int(vram_pct)}%")
                if "temp" in self.scopes:
                    temp = get_cpu_temp()
                    if temp is None:
                        temp = gpu.temp_c
                    if temp is not None:
                        self.scopes["temp"].update_value(temp, f"{int(temp)}°C")

            if self._battery_available:
                self.battery_widget.update_stats(get_battery())

            # Orchestrated timer tick
            self.countdown_timer.tick()
        except (psutil.Error, RuntimeError):
            LOGGER.exception("Failed to update taskbar monitor statistics")
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Unexpected error during stats update")

    # ------------------------------------------------------------------
    # Geometry & settings
    # ------------------------------------------------------------------
    def _resolve_screen_for_point(self, point: QPoint):
        """Return a QScreen whose availableGeometry contains point, else primary."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return None
        for screen in app.screens():
            if screen.availableGeometry().contains(point):
                return screen
        return app.primaryScreen()

    def load_geometry(self) -> None:
        """Load saved position and size with multi-monitor awareness."""
        x_pos = read_setting_int(self.settings, "pos_x", DEFAULT_POS)
        y_pos = read_setting_int(self.settings, "pos_y", DEFAULT_POS)
        width = read_setting_int(self.settings, "width", DEFAULT_WIDTH)
        height = read_setting_int(self.settings, "height", DEFAULT_HEIGHT)

        have_saved_pos = x_pos != DEFAULT_POS and y_pos != DEFAULT_POS
        target = QPoint(x_pos, y_pos) if have_saved_pos else QPoint()
        screen = self._resolve_screen_for_point(target) if have_saved_pos else None
        if have_saved_pos and screen is not None:
            # Saved position is valid only if it overlaps a connected monitor
            if screen.availableGeometry().contains(target):
                self.move(x_pos, y_pos)
            else:
                have_saved_pos = False

        if not have_saved_pos:
            app = QApplication.instance()
            screen = app.primaryScreen() if isinstance(app, QApplication) else None
            if screen is None:
                self.move(0, 0)
            else:
                available = screen.availableGeometry()
                self.move(
                    available.x() + available.width() - DEFAULT_FALLBACK_WIDTH,
                    available.y() + available.height() - DEFAULT_SCREEN_PAD,
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

    # ------------------------------------------------------------------
    # Painting & events
    # ------------------------------------------------------------------
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

    def changeEvent(self, a0) -> None:  # pylint: disable=invalid-name
        """Minimize into tray when the user minimizes the window."""
        if a0 is not None and a0.type() == QEvent.Type.WindowStateChange:
            if self.minimize_to_tray and self.isMinimized():
                QTimer.singleShot(0, self.hide)
        super().changeEvent(a0)

    def closeEvent(self, a0) -> None:  # pylint: disable=invalid-name
        """Cleanup shortcuts on close."""
        self.shortcut_service.unregister_all()
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(a0)


def main() -> int:
    """Run the taskbar monitor application."""
    log_path = os.path.join(os.path.dirname(__file__), "taskbar_monitor.log")
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path, encoding="utf-8")],
    )
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep running when tray-only
    psutil.cpu_percent(interval=CPU_WARMUP_INTERVAL_SECONDS)
    monitor = TaskbarMonitor()
    monitor.show()
    # Prime per-process CPU baselines lazily so startup isn't blocked iterating
    # hundreds of processes (this only affects the first sample of the Top
    # Processes popup, which already shows a "Loading…" placeholder).
    QTimer.singleShot(0, prime_process_cpu)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

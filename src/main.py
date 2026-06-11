import ctypes
import logging
import os
import sys

import psutil
from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QRect,
    QSettings,
    Qt,
    QThread,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QContextMenuEvent,
    QDesktopServices,
    QIcon,
    QImage,
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
    runtime_log_path,
)
from core.theme import DEFAULT_THEME_MODE, THEME_MODES, ThemeEngine
from services.app_chord_service import AppChordService, load_chord_entries
from services.cleanup_runner import CleanupRunner, provide_kill_response
from services.clipboard_history_service import ClipboardHistoryService
from services.microphone_recorder import (
    MicrophoneRecorder,
    MicrophoneRecorderError,
    load_recording_settings,
)
from services.notification_service import NotificationService

# UI
from services.process_snapshot import ProcessSnapshot
from services.resource_control import CleanupMode, CleanupScope, diff_snapshot_to_live

# Services
from services.resource_manager import (
    load_active_aggressive_profile,
    load_active_smart_profile,
)
from services.screenshot_service import (
    ScrollingScreenshotCoordinator,
    crop_screen_snapshot,
    element_rects_for_screen,
    get_foreground_window,
    grab_screen_region,
    grab_screen_snapshot,
    grab_window_pixmap,
    is_valid_capture_window,
    window_selections_from_qt_point,
)
from services.shortcut_service import ShortcutService
from services.system_info import (
    foreground_is_fullscreen,
    get_battery,
    get_cpu_temp,
    get_gpu_stats,
    get_ram_temp,
    start_background_pollers,
    stop_background_pollers,
)
from ui.app_chord_dialog import open_app_chord_manager
from ui.battery_widget import BatteryWidget
from ui.cleanup_history_dialog import open_cleanup_history_dialog
from ui.cleanup_result_dialog import open_cleanup_result_dialog
from ui.clipboard_popup import ClipboardHistoryPopup
from ui.cmdline_kill_dialog import open_cmdline_kill_dialog
from ui.kill_confirm_dialog import confirm_kill
from ui.menu_handler import AutostartManager, ContextMenuHandler
from ui.process_popup import TopProcessesPopup
from ui.recording_settings_dialog import open_recording_settings_dialog
from ui.snapshot_live_cleanup_dialog import select_snapshot_extra_processes
from ui.snapshot_manager_dialog import open_snapshot_manager
from ui.system_tray import build_tray
from ui.timer_widget import CountdownTimerWidget
from ui.widgets import CPUBarWidget, DragHandle, ScopeWidget

# Layout density presets — (margin_h, margin_v, spacing, scope_min_width, btn_size)
LAYOUT_PRESETS: dict[str, tuple[int, int, int, int, int]] = {
    "compact":  (4, 2, 4,  50, 18),
    "standard": (10, 5, 12, 70, 24),
    "roomy":    (14, 8, 18, 100, 28),
}
DEFAULT_LAYOUT_MODE = "standard"

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
    request_capture_regional = pyqtSignal()
    request_capture_active = pyqtSignal()
    request_capture_scrolling = pyqtSignal()
    request_capture_last_region = pyqtSignal()
    request_capture_element = pyqtSignal()

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
        self.request_capture_regional.connect(self.capture_regional)
        self.request_capture_active.connect(self.capture_active_window)
        self.request_capture_scrolling.connect(self.capture_scrolling)
        self.request_capture_last_region.connect(self.capture_last_region)
        self.request_capture_element.connect(self.capture_element)
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

        # Load last capture region
        rect_x = read_setting_int(self.settings, "last_capture_rect_x", -1)
        rect_y = read_setting_int(self.settings, "last_capture_rect_y", -1)
        rect_w = read_setting_int(self.settings, "last_capture_rect_w", -1)
        rect_h = read_setting_int(self.settings, "last_capture_rect_h", -1)
        self.last_capture_screen_name = self.settings.value("last_capture_screen_name", "")
        if rect_x != -1 and rect_y != -1 and rect_w != -1 and rect_h != -1:
            from PyQt6.QtCore import QRect
            self.last_capture_rect = QRect(rect_x, rect_y, rect_w, rect_h)
        else:
            self.last_capture_rect = None

        self.old_net = psutil.net_io_counters()
        self.old_disk = psutil.disk_io_counters()
        self.clipboard = QApplication.clipboard()
        if self.clipboard is None:
            raise RuntimeError("QApplication clipboard is not available")
        self.clipboard_history = ClipboardHistoryService(self.settings)
        self.clipboard.dataChanged.connect(self._on_clipboard_changed)

        self.m_drag = False
        self.m_resize = False
        self.m_resize_edge = ""
        self.m_drag_pos = QPoint()
        self._hwnd: int = 0
        self._topmost_applied = False
        self._hidden_for_fullscreen = False
        # Background cleanup worker state (off-UI release_resources execution).
        self._cleanup_in_flight: bool = False
        self._cleanup_thread: QThread | None = None
        self._cleanup_runner: CleanupRunner | None = None

        self.menu_handler = ContextMenuHandler(self)

        # Theme — load saved mode, register paint/restyle listener, hook OS scheme changes
        saved_theme = self.settings.value("theme_mode", DEFAULT_THEME_MODE)
        if not isinstance(saved_theme, str) or saved_theme not in THEME_MODES:
            saved_theme = DEFAULT_THEME_MODE
        ThemeEngine.set_mode(saved_theme)
        ThemeEngine.add_listener(self._on_theme_changed)
        try:
            from PyQt6.QtGui import QGuiApplication
            hints = QGuiApplication.styleHints()
            if hints is not None and hasattr(hints, "colorSchemeChanged"):
                hints.colorSchemeChanged.connect(
                    lambda _scheme: ThemeEngine.system_scheme_changed()
                )
        except (ImportError, AttributeError, RuntimeError):
            pass

        # Probe optional capabilities before building UI
        self._gpu_available = get_gpu_stats().available
        self._battery_available = get_battery() is not None
        self._temp_available = True

        self.process_popup: TopProcessesPopup | None = None
        self.clipboard_popup: ClipboardHistoryPopup | None = None
        self.selectors: list[QWidget] = []
        self._last_release_error_count = 0
        self._smart_profile = load_active_smart_profile(self.settings)
        self._aggressive_profile = load_active_aggressive_profile(self.settings)
        self._recording_settings = load_recording_settings(self.settings)
        self._microphone_recorder = MicrophoneRecorder(self._recording_settings)

        self.setup_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_stats)
        self.timer.timeout.connect(self._check_fullscreen_autohide)
        self.timer.start(self.interval)

        # Topmost enforcement: nativeEvent rewrites WM_WINDOWPOSCHANGING in
        # real time so this timer only exists as a safety net for cases where
        # no Z-order change message is generated (rare). 500ms was overkill —
        # 2s keeps DWM/Z-order recompute cost off the hot path.
        self.topmost_timer = QTimer(self)
        self.topmost_timer.setInterval(2000)
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

        # App chord shortcuts — per-app chords that forward keystrokes
        self.app_chord_service = AppChordService(
            notify=lambda title, msg: NotificationService.notify(title, msg),
        )
        failed_chords = self.app_chord_service.reload(load_chord_entries(self.settings))
        if failed_chords:
            NotificationService.notify(
                APP_NAME,
                f"Could not register {len(failed_chords)} chord prefix(es): "
                + ", ".join(failed_chords),
            )

        # Scrolling screenshot coordinator.
        # TEMP (scroll-capture diagnostics): dump every capture's raw frames +
        # stitched result to .intelag/reports/scroll_live so failures can be
        # inspected. Remove debug_dir once scrolling capture is confirmed good.
        from pathlib import Path as _Path

        scroll_debug_dir = str(
            _Path(__file__).resolve().parents[1] / ".intelag" / "reports" / "scroll_live"
        )
        self.scrolling_coordinator = ScrollingScreenshotCoordinator(
            self, debug_dir=scroll_debug_dir
        )
        self.scrolling_coordinator.finished.connect(self._on_scrolling_capture_finished)
        self.scrolling_coordinator.failed.connect(self._on_scrolling_capture_failed)

        # System tray
        self.tray = build_tray(
            parent=self,
            icon_path=self._icon_path,
            on_toggle_visibility=self.toggle_visibility,
            on_release_smart=lambda: self._on_release_resources(aggressive=False),
            on_release_aggressive=lambda: self._on_release_resources(aggressive=True),
            on_show_processes=self.show_processes_popup,
            on_show_clipboard=self.show_clipboard_popup,
            on_show_snapshots=self.show_snapshot_manager,
            on_show_cmdline_kill=self.show_cmdline_kill_dialog,
            on_show_app_chord_manager=self.show_app_chord_manager,
            on_show_cleanup_history=self.show_cleanup_history,
            get_is_recording=lambda: self.is_microphone_recording(),
            on_toggle_recording=self.toggle_microphone_recording,
            on_open_recordings_folder=self.open_recordings_folder,
            on_show_recording_settings=self.show_recording_settings,
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

    def nativeEvent(self, event_type, message):  # noqa: N802  # pylint: disable=invalid-name
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

    def showEvent(self, a0) -> None:  # noqa: N802  # pylint: disable=invalid-name
        """Re-apply topmost every time the window becomes visible."""
        super().showEvent(a0)
        # Synchronous re-apply (mirrors the originally-working pattern).
        # winId() will force HWND creation if not yet realized.
        self._apply_win32_topmost()
        self._enforce_topmost()

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
                    # Remove both LAYERED and TRANSPARENT — leaving LAYERED on
                    # causes Windows 11 DWM to mis-handle topmost z-order.
                    cur &= ~(_WS_EX_TRANSPARENT | _WS_EX_LAYERED)
                user32.SetWindowLongW(self._hwnd, _GWL_EXSTYLE, cur)
                # Force topmost re-assertion so the change doesn't demote us.
                user32.SetWindowPos(
                    self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                    _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
                )
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

    def show_clipboard_popup(self) -> None:
        """Open the clipboard history popup."""
        if self.clipboard_popup is None:
            self.clipboard_popup = ClipboardHistoryPopup(
                self.clipboard_history,
                self.clipboard,
            )
        self.clipboard_popup.refresh()
        popup_size = self.clipboard_popup.sizeHint()
        popup_width = max(self.clipboard_popup.width(), popup_size.width())
        popup_height = max(self.clipboard_popup.height(), popup_size.height())
        popup_x = self.x() + max((self.width() - popup_width) // 2, 0)
        popup_y = max(0, self.y() - popup_height)
        self.clipboard_popup.resize(popup_width, popup_height)
        self.clipboard_popup.move(popup_x, popup_y)
        self.clipboard_popup.show()
        self.clipboard_popup.raise_()
        self.clipboard_popup.activateWindow()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def setup_ui(self) -> None:
        """Create child widgets and layout."""
        self.main_layout = QHBoxLayout(self)
        self.main_layout.setContentsMargins(10, 5, 10, 5)
        self.main_layout.setSpacing(12)

        self.drag_handle = DragHandle(self)
        self.main_layout.addWidget(self.drag_handle)

        theme = ThemeEngine.current()
        btn_style = theme.button_qss
        recording_btn_style = theme.mic_recording_qss
        self._mic_button_idle_style = btn_style
        self._mic_button_recording_style = recording_btn_style

        # AutoSmart Button — uses the active "smart" profile
        self.smart_btn = QPushButton("🧠", self)
        self.smart_btn.setToolTip(
            f"AutoSmart ({self._smart_profile.name}): Release memory "
            "[Ctrl+Shift+Alt+Delete]"
        )
        self.smart_btn.setFixedSize(24, 24)
        self.smart_btn.setStyleSheet(btn_style)
        self.smart_btn.clicked.connect(lambda: self._on_release_resources(aggressive=False))
        self.main_layout.addWidget(self.smart_btn)

        # Aggressive Button — uses the active "aggressive" profile
        self.aggressive_btn = QPushButton("⚡", self)
        self.aggressive_btn.setToolTip(
            f"Aggressive ({self._aggressive_profile.name}): Deep memory cleanup "
            "[Ctrl+Shift+Alt+Backspace]"
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

        self.clipboard_btn = QPushButton("📋", self)
        self.clipboard_btn.setToolTip("Clipboard history combiner")
        self.clipboard_btn.setFixedSize(24, 24)
        self.clipboard_btn.setStyleSheet(btn_style)
        self.clipboard_btn.clicked.connect(self.show_clipboard_popup)
        self.main_layout.addWidget(self.clipboard_btn)

        # Snapshot manager — capture the running process set for later diff/cleanup
        self.snapshot_btn = QPushButton("📸", self)
        self.snapshot_btn.setToolTip(
            "Process snapshots: capture the running process list, then later "
            "use a snapshot as a reference to clean up only what's been added since."
        )
        self.snapshot_btn.setFixedSize(24, 24)
        self.snapshot_btn.setStyleSheet(btn_style)
        self.snapshot_btn.clicked.connect(self.show_snapshot_manager)
        self.main_layout.addWidget(self.snapshot_btn)

        self.mic_btn = QPushButton("🎙", self)
        self.mic_btn.setToolTip(
            "Start microphone recording to MP3.\n"
            "Uses shared input access and does not request exclusive control of the mic."
        )
        self.mic_btn.setFixedSize(24, 24)
        self.mic_btn.setStyleSheet(self._mic_button_idle_style)
        self.mic_btn.clicked.connect(self._toggle_microphone_recording)
        self.main_layout.addWidget(self.mic_btn)

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
            self.scopes["temp"].sec_min = 110.0
            self.scopes["temp"].sec_max = 180.0

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

        self._apply_scope_visibility()
        self._apply_layout_mode(self.get_layout_mode())

    # ------------------------------------------------------------------
    # Scope visibility & layout density
    # ------------------------------------------------------------------
    def is_scope_visible(self, key: str) -> bool:
        """Return whether the given scope is currently shown (default True)."""
        return bool(read_setting_int(self.settings, f"scope_visible_{key}", 1))

    def set_scope_visible(self, key: str, visible: bool) -> None:
        """Persist and apply visibility for a single scope."""
        self.settings.setValue(f"scope_visible_{key}", 1 if visible else 0)
        self.settings.sync()
        self._apply_scope_visibility()

    def _apply_scope_visibility(self) -> None:
        for key, scope in self.scopes.items():
            scope.setVisible(self.is_scope_visible(key))

    # ------------------------------------------------------------------
    # Theme
    # ------------------------------------------------------------------
    def get_theme_mode(self) -> str:
        return ThemeEngine.get_mode()

    def set_theme_mode(self, mode: str) -> None:
        if mode not in THEME_MODES:
            return
        self.settings.setValue("theme_mode", mode)
        self.settings.sync()
        ThemeEngine.set_mode(mode)

    def _on_theme_changed(self, theme) -> None:
        """Re-apply button styles and trigger repaints when the theme changes."""
        self._mic_button_idle_style = theme.button_qss
        self._mic_button_recording_style = theme.mic_recording_qss
        for btn in (self.smart_btn, self.aggressive_btn, self.procs_btn,
                    self.clipboard_btn, self.snapshot_btn):
            btn.setStyleSheet(theme.button_qss)
        if self._microphone_recorder.is_recording:
            self.mic_btn.setStyleSheet(theme.mic_recording_qss)
        else:
            self.mic_btn.setStyleSheet(theme.button_qss)
        self.update()
        self.cpu_grid.update()
        for scope in self.scopes.values():
            scope.grid_pixmap = None
            scope.update()
        if self._battery_available:
            self.battery_widget.update()

    def get_layout_mode(self) -> str:
        mode = self.settings.value("layout_mode", DEFAULT_LAYOUT_MODE)
        if not isinstance(mode, str) or mode not in LAYOUT_PRESETS:
            return DEFAULT_LAYOUT_MODE
        return mode

    def set_layout_mode(self, mode: str) -> None:
        if mode not in LAYOUT_PRESETS:
            return
        self.settings.setValue("layout_mode", mode)
        self.settings.sync()
        self._apply_layout_mode(mode)

    def _apply_layout_mode(self, mode: str) -> None:
        margin_h, margin_v, spacing, scope_min_w, btn_size = LAYOUT_PRESETS[mode]
        self.main_layout.setContentsMargins(margin_h, margin_v, margin_h, margin_v)
        self.main_layout.setSpacing(spacing)
        for btn in (self.smart_btn, self.aggressive_btn, self.procs_btn,
                    self.clipboard_btn, self.snapshot_btn, self.mic_btn):
            btn.setFixedSize(btn_size, btn_size)
        for scope in self.scopes.values():
            scope.setMinimumWidth(scope_min_w)
        self.adjustSize()

    def show_snapshot_manager(self) -> None:
        """Open the process-snapshot manager dialog."""
        open_snapshot_manager(parent=self, on_clean=self._clean_using_snapshot)

    def show_cleanup_history(self) -> None:
        """Open the cleanup history dialog."""
        open_cleanup_history_dialog(parent=self)

    def show_cmdline_kill_dialog(self) -> None:
        """Kill processes whose WMI CommandLine matches a saved regex."""
        open_cmdline_kill_dialog(self.settings, parent=self)

    def show_app_chord_manager(self) -> None:
        """Open the app chord shortcuts manager.

        The chord service is paused while the dialog is open so the user can
        record any chord — including ones currently registered as global
        hotkeys, which the ``keyboard`` library would otherwise suppress
        before Qt's keyPressEvent could see them. The service is reloaded
        from the latest saved state when the dialog closes.
        """
        self.app_chord_service.unregister_all()
        try:
            open_app_chord_manager(
                self.settings,
                on_apply=None,  # don't re-register mid-dialog; we reload on close
                parent=self,
            )
        finally:
            self._on_app_chord_entries_changed(load_chord_entries(self.settings))

    def _on_app_chord_entries_changed(self, entries) -> None:
        """Re-register chord hotkeys after the user edits entries."""
        failed = self.app_chord_service.reload(entries)
        if failed:
            NotificationService.notify(
                APP_NAME,
                f"Could not register {len(failed)} chord prefix(es): "
                + ", ".join(failed),
            )

    def show_recording_settings(self) -> None:
        """Open the microphone recording settings dialog."""
        open_recording_settings_dialog(
            self.settings,
            on_apply=self.reload_recording_settings,
            parent=self,
        )

    def reload_recording_settings(self) -> None:
        """Reload microphone recording settings from QSettings."""
        self._recording_settings = load_recording_settings(self.settings)
        self._microphone_recorder.update_settings(self._recording_settings)
        self._set_microphone_button_state(
            self._microphone_recorder.is_recording,
            self._microphone_recorder.active_session,
        )

    def open_recordings_folder(self) -> None:
        """Open the configured recordings directory in the OS shell."""
        folder = self._format_notification_path(self._recording_settings.effective_output_dir())
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(folder)):
            NotificationService.notify(
                APP_NAME,
                f"Could not open the recordings folder.\nPath:\n{folder}",
            )

    def is_microphone_recording(self) -> bool:
        """Return whether microphone recording is active."""
        return self._microphone_recorder.is_recording

    def toggle_microphone_recording(self) -> None:
        """Public wrapper used by menus and tray actions."""
        self._toggle_microphone_recording()

    def _clean_using_snapshot(self, snapshot: ProcessSnapshot) -> None:
        """Preview and kill only the extra processes that appeared after a snapshot.

        Selection happens on the UI thread (it shows the picker dialog), then
        the actual cleanup runs on the worker thread so the UI doesn't freeze.
        """
        if self._cleanup_in_flight:
            return
        profile = self._aggressive_profile
        try:
            diff = diff_snapshot_to_live(snapshot)
            selected_pids = select_snapshot_extra_processes(
                self.settings,
                diff,
                parent=self,
            )
            if selected_pids is None:
                return
            scope = CleanupScope(
                mode=CleanupMode.SNAPSHOT_EXTRAS.value,
                snapshot_name=snapshot.name,
                candidate_pids=frozenset(extra.pid for extra in diff.extra_processes),
                target_pids=frozenset(selected_pids),
                snapshot_matched_count=diff.matched_count,
                snapshot_identity_collisions=diff.identity_collisions,
            )
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Snapshot-driven cleanup setup failed")
            return

        self._start_cleanup_worker(
            profile=profile,
            scope=scope,
            mode_label=f"{profile.name} Clear (vs '{snapshot.name}')",
        )

    def reload_resource_profiles(self) -> None:
        """Re-read smart/aggressive profile bindings from QSettings."""
        self._smart_profile = load_active_smart_profile(self.settings)
        self._aggressive_profile = load_active_aggressive_profile(self.settings)
        if self.smart_btn is not None:
            self.smart_btn.setToolTip(
                f"AutoSmart ({self._smart_profile.name}): Release memory "
                "[Ctrl+Shift+Alt+Delete]"
            )
        if self.aggressive_btn is not None:
            self.aggressive_btn.setToolTip(
                f"Aggressive ({self._aggressive_profile.name}): Deep memory cleanup "
                "[Ctrl+Shift+Alt+Backspace]"
            )

    def _toggle_microphone_recording(self) -> None:
        """Start or stop microphone recording."""
        if self._microphone_recorder.is_recording:
            self._stop_microphone_recording()
        else:
            self._start_microphone_recording()

    def _start_microphone_recording(self) -> None:
        """Start capturing microphone audio to an MP3 file."""
        try:
            session = self._microphone_recorder.start_recording()
        except MicrophoneRecorderError as exc:
            LOGGER.warning("Microphone recording could not start: %s", exc)
            NotificationService.notify(APP_NAME, str(exc))
            self._set_microphone_button_state(False)
            return
        LOGGER.info("Microphone recording started: %s", session.output_path)
        self._set_microphone_button_state(True, session)
        output_path = self._format_notification_path(session.output_path)
        NotificationService.notify(
            APP_NAME,
            f"Microphone recording started.\nPath:\n{output_path}\nDevice: {session.device_name}",
        )

    def _stop_microphone_recording(self) -> None:
        """Stop the active microphone recording and flush the MP3 file."""
        active_session = self._microphone_recorder.active_session
        try:
            session = self._microphone_recorder.stop_recording()
        except MicrophoneRecorderError as exc:
            LOGGER.warning("Microphone recording could not stop cleanly: %s", exc)
            NotificationService.notify(APP_NAME, str(exc))
            self._set_microphone_button_state(False)
            return
        self._set_microphone_button_state(False)
        target = session if active_session is None else active_session
        LOGGER.info("Microphone recording saved: %s", target.output_path)
        output_path = self._format_notification_path(target.output_path)
        NotificationService.notify(
            APP_NAME,
            f"Microphone recording saved.\nPath:\n{output_path}",
        )
        if self._recording_settings.open_folder_after_save:
            self.open_recordings_folder()

    def _set_microphone_button_state(self, recording: bool, session=None) -> None:
        """Refresh the microphone button appearance and tooltip."""
        if recording:
            self.mic_btn.setText("⏹")
            self.mic_btn.setStyleSheet(self._mic_button_recording_style)
            self.mic_btn.setToolTip(
                "Stop microphone recording and save the MP3.\n"
                f"Recording: {os.path.basename(session.output_path)}"
            )
            return
        self.mic_btn.setText("🎙")
        self.mic_btn.setStyleSheet(self._mic_button_idle_style)
        self.mic_btn.setToolTip(
            "Start microphone recording to MP3.\n"
            f"Folder: {self._recording_settings.effective_output_dir()}"
        )

    def _sync_microphone_recording_status(self) -> None:
        """Keep the button state in sync with the recorder worker state."""
        if self._microphone_recorder.is_recording:
            return
        if self.mic_btn.text() == "⏹":
            self._set_microphone_button_state(False)
            error = self._microphone_recorder.consume_last_error()
            if error:
                NotificationService.notify(APP_NAME, error)

    def _format_notification_path(self, path: str) -> str:
        """Normalize a filesystem path before showing it in notifications."""
        return os.path.abspath(os.path.normpath(path))

    def _on_release_resources(self, aggressive: bool = False) -> None:
        """Trigger resource release on a background thread; UI stays responsive.

        ``release_resources`` walks every running process via
        ``psutil.process_iter`` and performs Win32 working-set/standby
        operations. Running it on the UI thread froze the app for 5–20s on
        busy systems. We now drive it from a worker QThread and marshal the
        optional kill-confirmation dialog back to the UI thread via a
        queued signal in :class:`services.cleanup_runner.CleanupRunner`.
        """
        if getattr(self, "_cleanup_in_flight", False):
            return  # Re-entry guard: ignore clicks while a cleanup is running.

        btn = self.aggressive_btn if aggressive else self.smart_btn
        btn.setEnabled(False)
        old_text = btn.text()
        btn.setText("⏳")

        profile = self._aggressive_profile if aggressive else self._smart_profile
        self._start_cleanup_worker(
            profile=profile,
            scope=None,
            mode_label=f"{profile.name} Clear",
            on_done_btn=btn,
            on_done_btn_text=old_text,
        )

    def _start_cleanup_worker(
        self,
        *,
        profile,
        scope,
        mode_label: str,
        on_done_btn=None,
        on_done_btn_text: str = "",
    ) -> None:
        """Spin up the worker QThread and wire its signals to UI handlers."""
        self._cleanup_in_flight = True
        thread = QThread(self)
        runner = CleanupRunner(
            profile=profile,
            scope=scope,
            kill_dialog_title=f"Confirm {profile.name} Cleanup",
        )
        runner.moveToThread(thread)

        # Bound-method receiver lives on TaskbarMonitor (UI thread), so Qt
        # auto-selects a queued connection when the worker thread emits.
        runner.request_kill_dialog.connect(self._on_kill_dialog_request)
        runner.finished.connect(
            lambda result: self._on_cleanup_done(
                result, profile, mode_label, on_done_btn, on_done_btn_text, thread, runner,
            )
        )
        runner.failed.connect(
            lambda exc: self._on_cleanup_failed(
                exc, on_done_btn, on_done_btn_text, thread, runner,
            )
        )
        thread.started.connect(runner.run)
        # Track so __del__ doesn't yank them mid-flight if the user closes the window.
        self._cleanup_thread = thread
        self._cleanup_runner = runner
        thread.start()

    def _on_kill_dialog_request(self, candidates, response, title: str) -> None:
        """Runs on the UI thread (queued from worker). Show dialog, return result."""
        try:
            approved = confirm_kill(
                self,
                candidates,
                title=title,
                warning_prefix="background",
            )
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("confirm_kill dialog failed")
            approved = None
        provide_kill_response(response, approved)

    def _on_cleanup_done(
        self, result, profile, mode_label, btn, btn_text, thread, runner,
    ) -> None:
        try:
            LOGGER.info("Resource release (%s): %s", profile.name, result.summary)
            tooltip = f"Last freed: {result.ram_freed_gb:.2f} GB"
            if result.errors:
                tooltip += f" — {len(result.errors)} error(s)"
                self._last_release_error_count = len(result.errors)
            else:
                self._last_release_error_count = 0
            self._present_cleanup_result(mode_label, result)
            if btn is not None:
                btn.setToolTip(tooltip)
        finally:
            self._teardown_cleanup_worker(thread, runner)
            if btn is not None:
                QTimer.singleShot(600, lambda: self._restore_btn(btn, btn_text))

    def _on_cleanup_failed(self, exc, btn, btn_text, thread, runner) -> None:
        LOGGER.error("Resource release failed: %s", exc, exc_info=exc)
        if btn is not None:
            btn.setToolTip("Release failed — see log")
        self._teardown_cleanup_worker(thread, runner)
        if btn is not None:
            QTimer.singleShot(600, lambda: self._restore_btn(btn, btn_text))

    def _teardown_cleanup_worker(self, thread, runner) -> None:
        self._cleanup_in_flight = False
        try:
            thread.quit()
            thread.wait(2000)
        except RuntimeError:
            pass
        runner.deleteLater()
        thread.deleteLater()
        if getattr(self, "_cleanup_thread", None) is thread:
            self._cleanup_thread = None
            self._cleanup_runner = None

    def _present_cleanup_result(self, mode_name: str, result) -> None:
        """Show the cleanup toast and open diagnostics when needed."""
        NotificationService.notify_cleanup(mode_name, result)
        if result.errors or result.processes_cleaned_total == 0:
            title = f"{mode_name} Result"
            if result.errors:
                title = f"{mode_name} Result (partial)"
            open_cleanup_result_dialog(result, title=title, parent=self)

    def _restore_btn(self, btn: QPushButton, text: str) -> None:
        """Restore the release button state."""
        btn.setText(text)
        btn.setEnabled(True)

    def format_speed(self, bytes_per_second: float) -> str:
        """Format network throughput in K or M units."""
        if bytes_per_second >= MB:
            return f"{bytes_per_second / MB:.1f}M"
        return f"{bytes_per_second / KB:.0f}K"

    def _restore_after_screenshot(self) -> None:
        self.show()
        self.raise_()
        self._apply_win32_topmost()

    def _close_screenshot_selectors(self, *, restore: bool) -> None:
        for selector in list(self.selectors):
            selector.close()
        self.selectors.clear()
        if restore:
            self._restore_after_screenshot()

    def _screen_by_name(self, screen_name: str):
        for screen in QApplication.screens():
            if screen.name() == screen_name:
                return screen
        return QApplication.primaryScreen()

    def _store_last_capture_region(self, screen, local_rect: QRect) -> None:
        self.last_capture_rect = QRect(local_rect)
        self.last_capture_screen_name = screen.name()
        self.settings.setValue("last_capture_rect_x", local_rect.x())
        self.settings.setValue("last_capture_rect_y", local_rect.y())
        self.settings.setValue("last_capture_rect_w", local_rect.width())
        self.settings.setValue("last_capture_rect_h", local_rect.height())
        self.settings.setValue("last_capture_screen_name", screen.name())
        self.settings.sync()

    def _copy_pixmap_to_clipboard(self, pixmap, failure_message: str) -> bool:
        try:
            if pixmap.isNull():
                LOGGER.warning("%s", failure_message)
                NotificationService.notify(APP_NAME, failure_message)
                return False
            image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB32)
            self.clipboard.setImage(image)
            return True
        finally:
            self._restore_after_screenshot()

    def _copy_region_to_clipboard(
        self,
        screen,
        local_rect: QRect,
    ) -> bool:
        QApplication.processEvents()
        pixmap = grab_screen_region(screen, local_rect)
        return self._copy_pixmap_to_clipboard(
            pixmap,
            "Failed to capture screenshot region.",
        )

    def capture_regional(self) -> None:
        """Trigger regional screenshot using a custom interactive capture overlay."""
        self._close_screenshot_selectors(restore=False)
        self.hide()
        QApplication.processEvents()
        QTimer.singleShot(50, self._show_region_selectors)

    def _show_region_selectors(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        from ui.screenshot_overlay import RegionSelector

        screens = QGuiApplication.screens()
        if not screens:
            NotificationService.notify(APP_NAME, "No screens available for screenshot.")
            self._restore_after_screenshot()
            return

        screen_snapshots = []
        for screen in screens:
            snapshot = grab_screen_snapshot(screen)
            if not snapshot.isNull():
                screen_snapshots.append((screen, snapshot))

        if not screen_snapshots:
            NotificationService.notify(APP_NAME, "Failed to capture screen for selection.")
            self._restore_after_screenshot()
            return

        def on_selected(local_rect: QRect, screen, snapshot) -> None:
            selected_rect = QRect(local_rect)
            selected_screen = screen
            selected_snapshot = snapshot
            self._close_screenshot_selectors(restore=False)

            def capture_after_overlay_closes() -> None:
                pixmap = crop_screen_snapshot(
                    selected_snapshot,
                    selected_screen,
                    selected_rect,
                )
                copied = self._copy_pixmap_to_clipboard(
                    pixmap,
                    "Failed to capture screenshot region.",
                )
                if copied and not selected_rect.isEmpty():
                    self._store_last_capture_region(selected_screen, selected_rect)

            QTimer.singleShot(20, capture_after_overlay_closes)

        def on_cancelled() -> None:
            self._close_screenshot_selectors(restore=True)

        for screen, snapshot in screen_snapshots:
            selector = RegionSelector(screen, snapshot, on_selected, on_cancelled)
            self.selectors.append(selector)
            selector.show()

    def capture_element(self) -> None:
        """Trigger smart element capture using a hover-highlight overlay."""
        self._close_screenshot_selectors(restore=False)
        self.hide()
        QApplication.processEvents()
        QTimer.singleShot(80, self._show_element_selectors)

    def _show_element_selectors(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        from services.uia_service import collect_element_rects
        from ui.screenshot_overlay import ElementSelector

        screens = QGuiApplication.screens()
        if not screens:
            NotificationService.notify(APP_NAME, "No screens available for screenshot.")
            self._restore_after_screenshot()
            return

        # Resolve element rectangles once, while the desktop is still uncovered,
        # then hit-test them locally on each per-screen overlay.
        native_rects = collect_element_rects()
        screen_data = []
        for screen in screens:
            snapshot = grab_screen_snapshot(screen)
            if not snapshot.isNull():
                rects = element_rects_for_screen(screen, native_rects)
                screen_data.append((screen, snapshot, rects))

        if not screen_data:
            NotificationService.notify(APP_NAME, "Failed to capture screen for selection.")
            self._restore_after_screenshot()
            return

        def on_selected(local_rect: QRect, screen, snapshot) -> None:
            selected_rect = QRect(local_rect)
            selected_screen = screen
            selected_snapshot = snapshot
            self._close_screenshot_selectors(restore=False)

            def capture_after_overlay_closes() -> None:
                pixmap = crop_screen_snapshot(
                    selected_snapshot,
                    selected_screen,
                    selected_rect,
                )
                copied = self._copy_pixmap_to_clipboard(
                    pixmap,
                    "Failed to capture element.",
                )
                if copied and not selected_rect.isEmpty():
                    self._store_last_capture_region(selected_screen, selected_rect)

            QTimer.singleShot(20, capture_after_overlay_closes)

        def on_cancelled() -> None:
            self._close_screenshot_selectors(restore=True)

        for screen, snapshot, rects in screen_data:
            selector = ElementSelector(screen, snapshot, rects, on_selected, on_cancelled)
            self.selectors.append(selector)
            selector.show()

    def capture_last_region(self) -> None:
        """Repeat screenshot of the last captured region."""
        if self.last_capture_rect is None or not self.last_capture_screen_name:
            NotificationService.notify(
                APP_NAME,
                "No previous regional screenshot found to repeat.",
            )
            return

        target_screen = self._screen_by_name(str(self.last_capture_screen_name))
        if target_screen is None:
            NotificationService.notify(APP_NAME, "No screen found for repeating screenshot.")
            return

        local_rect = QRect(self.last_capture_rect)
        self.hide()
        QApplication.processEvents()
        QTimer.singleShot(
            80,
            lambda: self._copy_region_to_clipboard(
                target_screen,
                local_rect,
            ),
        )

    def capture_active_window(self) -> None:
        """Capture the currently active foreground window to the clipboard."""
        hwnd = get_foreground_window()

        try:
            own_hwnd = int(self.winId())
        except (AttributeError, ValueError):
            own_hwnd = 0

        if not is_valid_capture_window(hwnd, own_hwnd):
            LOGGER.warning("Active window is invalid for screenshot.")
            NotificationService.notify(APP_NAME, "No active window found for screenshot.")
            return

        self.hide()
        QApplication.processEvents()
        QTimer.singleShot(50, lambda: self._capture_window_to_clipboard(hwnd))

    def _capture_window_to_clipboard(self, hwnd: int) -> None:
        try:
            pixmap = grab_window_pixmap(hwnd)
            if pixmap.isNull():
                LOGGER.warning("Active window screenshot returned a null pixmap.")
                NotificationService.notify(APP_NAME, "Failed to capture active window.")
                return
            self.clipboard.setPixmap(pixmap)
        finally:
            self._restore_after_screenshot()

    def capture_scrolling(self) -> None:
        """Trigger scrolling screenshot by first letting the user click a target window."""
        self._close_screenshot_selectors(restore=False)
        self.hide()
        QApplication.processEvents()
        QTimer.singleShot(50, self._show_scroll_selectors)

    def _show_scroll_selectors(self) -> None:
        from PyQt6.QtGui import QGuiApplication

        from ui.screenshot_overlay import ScrollSelector

        screens = QGuiApplication.screens()
        if not screens:
            NotificationService.notify(APP_NAME, "No screens available for screenshot.")
            self._restore_after_screenshot()
            return

        def on_selected(
            global_pos: QPoint,
            viewport_rect: QRect | None,
            viewport_screen,
        ) -> None:
            clicked_pos = QPoint(global_pos)
            selected_viewport_rect = QRect(viewport_rect) if viewport_rect is not None else QRect()
            selected_viewport_screen = viewport_screen
            selector_hwnds: set[int] = set()
            for selector in self.selectors:
                try:
                    selector_hwnds.add(int(selector.winId()))
                except (AttributeError, ValueError):
                    pass
            self._close_screenshot_selectors(restore=False)

            def start_after_overlay_closes() -> None:
                try:
                    own_hwnd = int(self.winId())
                except (AttributeError, ValueError):
                    own_hwnd = 0
                excluded_hwnds = set(selector_hwnds)
                if own_hwnd:
                    excluded_hwnds.add(own_hwnd)

                for selection in window_selections_from_qt_point(clicked_pos):
                    if (
                        selection.capture_hwnd in excluded_hwnds
                        or selection.scroll_hwnd in excluded_hwnds
                    ):
                        continue
                    if not is_valid_capture_window(selection.capture_hwnd, own_hwnd):
                        continue
                    self.scrolling_coordinator.start(
                        selection.capture_hwnd,
                        selection.scroll_hwnd,
                        selected_viewport_screen,
                        selected_viewport_rect,
                    )
                    return

                NotificationService.notify(APP_NAME, "No window found at clicked location.")
                self._restore_after_screenshot()

            QTimer.singleShot(120, start_after_overlay_closes)

        def on_cancelled() -> None:
            self._close_screenshot_selectors(restore=True)

        for screen in screens:
            selector = ScrollSelector(screen, on_selected, on_cancelled)
            self.selectors.append(selector)
            selector.show()

    def _on_scrolling_capture_finished(self, image: QImage) -> None:
        """Called when scrolling capture sequence completes successfully."""
        if self.clipboard is not None:
            self.clipboard.setImage(image)
        self.show()
        self.raise_()
        self._apply_win32_topmost()

    def _on_scrolling_capture_failed(self, reason: str) -> None:
        """Called when scrolling capture sequence fails."""
        NotificationService.notify(
            APP_NAME,
            f"Scrolling screenshot failed: {reason}"
        )
        self.show()
        self.raise_()
        self._apply_win32_topmost()

    def update_stats(self) -> None:
        """Poll system stats and refresh monitor widgets."""
        try:
            self._sync_microphone_recording_status()
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
                    c_temp = get_cpu_temp()
                    if c_temp is None:
                        c_temp = gpu.temp_c
                    c_temp_f = c_temp * 9 / 5 + 32 if c_temp is not None else None

                    r_temp = get_ram_temp()
                    r_temp_f = r_temp * 9 / 5 + 32 if r_temp is not None else None

                    text = ""
                    if c_temp_f is not None:
                        text += f"CPU: {int(c_temp_f)}°F"
                    if r_temp_f is not None:
                        text += f" RAM: {int(r_temp_f)}°F"

                    self.scopes["temp"].update_value(
                        value=c_temp_f if c_temp_f is not None else 0.0,
                        text=text.strip() if text else "N/A",
                        auto_scale=True,
                        secondary_value=r_temp_f
                    )

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
    def paintEvent(self, a0: QPaintEvent | None) -> None:  # noqa: N802  # pylint: disable=invalid-name
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

    def mousePressEvent(self, a0: QMouseEvent | None) -> None:  # noqa: N802  # pylint: disable=invalid-name
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

    def mouseMoveEvent(self, a0: QMouseEvent | None) -> None:  # noqa: N802  # pylint: disable=invalid-name
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

    def mouseReleaseEvent(self, a0: QMouseEvent | None) -> None:  # noqa: N802  # pylint: disable=invalid-name
        """Handle mouse release."""
        del a0
        self.m_drag = False
        self.m_resize = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        self.save_geometry()

    def contextMenuEvent(self, a0: QContextMenuEvent | None) -> None:  # noqa: N802  # pylint: disable=invalid-name
        """Handle context menu event."""
        if a0 is not None:
            self.menu_handler.handle_event(a0)

    def changeEvent(self, a0) -> None:  # noqa: N802  # pylint: disable=invalid-name
        """Minimize into tray when the user minimizes the window."""
        if a0 is not None and a0.type() == QEvent.Type.WindowStateChange:
            if self.minimize_to_tray and self.isMinimized():
                QTimer.singleShot(0, self.hide)
        super().changeEvent(a0)

    def closeEvent(self, a0) -> None:  # noqa: N802  # pylint: disable=invalid-name
        """Cleanup shortcuts on close."""
        if self._microphone_recorder.is_recording:
            try:
                self._microphone_recorder.stop_recording()
            except MicrophoneRecorderError:
                LOGGER.exception("Failed to stop microphone recording during shutdown")
        self.shortcut_service.unregister_all()
        self.app_chord_service.unregister_all()
        stop_background_pollers()
        if self.tray is not None:
            self.tray.hide()
        super().closeEvent(a0)

    def _on_clipboard_changed(self) -> None:
        """Track clipboard text changes for the history popup."""
        text = self.clipboard.text(mode=self.clipboard.Mode.Clipboard)
        self.clipboard_history.sync_text(text)
        if self.clipboard_popup is not None and self.clipboard_popup.isVisible():
            self.clipboard_popup.refresh()


def main() -> int:
    """Run the taskbar monitor application."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(runtime_log_path(), encoding="utf-8"),
        ],
    )
    # Set Per-Monitor DPI awareness before creating QApplication
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(-4)
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)  # keep running when tray-only
    psutil.cpu_percent(interval=CPU_WARMUP_INTERVAL_SECONDS)
    monitor = TaskbarMonitor()
    monitor.show()

    # Belt-and-suspenders: the originally-working code applied Win32 topmost
    # styles SYNCHRONOUSLY right after show(). Keep that call here in addition
    # to the showEvent re-apply, so the HWND is guaranteed pinned before the
    # event loop even spins up.
    monitor._apply_win32_topmost()
    monitor._enforce_topmost()

    # No startup CPU prime: iterating every running process holds the GIL for
    # 10–20s on busy systems, which made the just-shown window unresponsive
    # (freezing right-clicks and other interactions). The Top Processes popup
    # has its own QThread that polls every 2.5s, so the first emit shows
    # 0% CPU briefly and the next emit shows real values.
    # Background LHM/temperature poller — keeps HTTP off the UI thread.
    QTimer.singleShot(0, start_background_pollers)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

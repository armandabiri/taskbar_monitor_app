import ctypes
import logging
import os
import sys

import psutil
from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QSettings,
    Qt,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QContextMenuEvent,
    QDesktopServices,
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
    MIN_WIDGET_HEIGHT,
    MIN_WIDGET_WIDTH,
    read_setting_int,
    runtime_log_path,
)
from core.theme import DEFAULT_THEME_MODE, THEME_MODES, ThemeEngine
from services.app_chord_service import AppChordService, load_chord_entries
from services.auto_clean_watchdog import (
    AutoCleanWatchdog,
    load_auto_clean_config,
)
from services.clipboard_history_service import ClipboardHistoryService
from services.microphone_recorder import (
    MicrophoneRecorder,
    MicrophoneRecorderError,
    load_recording_settings,
)
from services.notification_service import NotificationService

# UI
from services.process_snapshot import ProcessSnapshot

# Services
from services.resource_manager import (
    load_active_aggressive_profile,
    load_active_smart_profile,
)
from services.sensors import get_hub
from services.shortcut_service import ShortcutService
from services.system_info import (
    foreground_is_fullscreen,
    get_battery,
    get_gpu_stats,
    start_background_pollers,
)
from services.system_sampler import SystemSampler, choose_interval
from ui.app_chord_dialog import open_app_chord_manager
from ui.battery_widget import BatteryWidget
from ui.capture_controller import CaptureController
from ui.cleanup_controller import CleanupController
from ui.cleanup_history_dialog import open_cleanup_history_dialog
from ui.clipboard_popup import ClipboardHistoryPopup
from ui.cmdline_kill_dialog import open_cmdline_kill_dialog
from ui.menu_handler import AutostartManager, ContextMenuHandler
from ui.monitor_lifecycle import MonitorLifecycle
from ui.process_popup import TopProcessesPopup
from ui.recording_settings_dialog import open_recording_settings_dialog
from ui.scope_manager import ScopeManager
from ui.snapshot_manager_dialog import open_snapshot_manager
from ui.system_tray import build_tray
from ui.timer_widget import CountdownTimerWidget
from ui.topmost_controller import TopmostController
from ui.widgets import DragHandle

# Layout density presets — (margin_h, margin_v, spacing, scope_min_width, btn_size)
LAYOUT_PRESETS: dict[str, tuple[int, int, int, int, int]] = {
    "compact":  (4, 2, 4,  50, 18),
    "standard": (10, 5, 12, 70, 24),
    "roomy":    (14, 8, 18, 100, 28),
}
DEFAULT_LAYOUT_MODE = "standard"

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
    request_toggle_click_through = pyqtSignal()
    request_capture_regional = pyqtSignal()
    request_capture_active = pyqtSignal()
    request_capture_scrolling = pyqtSignal()
    request_capture_last_region = pyqtSignal()
    request_capture_element = pyqtSignal()
    request_capture_full_screen = pyqtSignal()
    request_capture_full_desktop = pyqtSignal()
    request_pin_capture = pyqtSignal()
    request_toggle_collection = pyqtSignal()
    request_paste_collection = pyqtSignal()

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
        self.request_capture_full_screen.connect(self.capture_full_screen)
        self.request_capture_full_desktop.connect(self.capture_full_desktop)
        self.request_pin_capture.connect(self.pin_last_capture)
        self.request_toggle_collection.connect(self.toggle_capture_collection)
        self.request_paste_collection.connect(self.paste_capture_collection)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)

        self.settings = QSettings(APP_ORG, APP_NAME)
        self.interval = read_setting_int(self.settings, "interval", DEFAULT_INTERVAL_MS)
        self._active_interval_ms = read_setting_int(
            self.settings, "sampler/active_interval_ms", DEFAULT_INTERVAL_MS
        )
        self._hidden_interval_ms = read_setting_int(
            self.settings, "sampler/hidden_interval_ms", 5000
        )
        self._pause_on_battery = bool(read_setting_int(
            self.settings, "sampler/pause_on_battery", 0
        ))
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

        self.clipboard = QApplication.clipboard()
        if self.clipboard is None:
            raise RuntimeError("QApplication clipboard is not available")
        self.clipboard_history = ClipboardHistoryService(self.settings)
        self.clipboard.dataChanged.connect(self._on_clipboard_changed)

        self.m_drag = False
        self.m_resize = False
        self.m_resize_edge = ""
        self.m_drag_pos = QPoint()
        self._topmost = TopmostController()
        self._topmost.set_click_through_preference(self.click_through)
        self.lifecycle = MonitorLifecycle()
        self._hidden_for_fullscreen = False
        # Cleanup worker lifecycle + dialogs are owned by CleanupController,
        # created after setup_ui() once the smart/aggressive buttons exist.

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

        self.sampler = SystemSampler(parent=self)
        self.sampler.snapshot_ready.connect(self._render_snapshot)
        self.sampler.start_worker(self.interval)
        self.lifecycle.register("sampler_worker", self.sampler.stop_worker)

        # UI-thread timer drives only UI-local periodic checks (microphone
        # sync, fullscreen autohide). Heavy sampling runs in the sampler's
        # worker thread.
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_stats)
        self.timer.timeout.connect(self._check_fullscreen_autohide)
        self.timer.start(self.interval)
        self.lifecycle.register("update_timer", self.timer.stop)

        self.topmost_timer = self._topmost.start_safety_timer(self, 2000)
        self.lifecycle.register("topmost_timer", self._topmost.stop_safety_timer)

        # SensorHub owns its own thread + native handles (CLR computer, NVML).
        # Register here so shutdown stops reads, joins the thread, then closes
        # backends and calls nvmlShutdown in a race-free order.
        self.lifecycle.register("sensor_hub", get_hub().stop)

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

        # Scrolling screenshot coordinator (owned by capture controller).
        self.capture_controller = CaptureController(self)
        self._capture_toolbar = None

        # Resource-cleanup flow (worker lifecycle, progress, result dialogs).
        self.cleanup_controller = CleanupController(self)
        self._auto_clean_watchdog = AutoCleanWatchdog(
            load_auto_clean_config(self.settings),
            on_fire=self.cleanup_controller.auto_clean_fire,
        )

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
    # Window topmost enforcement (delegates to TopmostController)
    # ------------------------------------------------------------------
    def _apply_win32_topmost(self) -> None:
        self._topmost.attach(self)

    def _enforce_topmost(self) -> None:
        self._topmost.enforce()

    @property
    def _hwnd(self) -> int:
        return self._topmost.hwnd

    def nativeEvent(self, event_type, message):  # noqa: N802  # pylint: disable=invalid-name
        self._topmost.handle_native_event(event_type, message)
        return False, 0

    def showEvent(self, a0) -> None:  # noqa: N802  # pylint: disable=invalid-name
        super().showEvent(a0)
        self._topmost.attach(self)
        self._topmost.enforce()
        self._apply_cadence()

    def hideEvent(self, a0) -> None:  # noqa: N802  # pylint: disable=invalid-name
        super().hideEvent(a0)
        self._apply_cadence()

    def _apply_cadence(self) -> None:
        """Adjust the sampler interval for current visibility and power state."""
        bat = get_battery()
        on_battery = bat is not None and not bat.plugged
        interval = choose_interval(
            self._active_interval_ms,
            self._hidden_interval_ms,
            visible=self.isVisible(),
            on_battery=on_battery,
            pause_on_battery=self._pause_on_battery,
        )
        self.sampler.set_interval(interval)

    # ------------------------------------------------------------------
    # Click-through toggle
    # ------------------------------------------------------------------
    def set_click_through(self, enabled: bool) -> None:
        """Toggle click-through mode and persist the setting."""
        self.click_through = enabled
        self.settings.setValue("click_through", 1 if enabled else 0)
        self.settings.sync()
        self._topmost.set_click_through(enabled)
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
            self.process_popup = TopProcessesPopup(sampler=self.sampler)
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

        # CPU core grid + oscilloscope scopes (incl. GPU/SSD temperature) are
        # owned by ScopeManager, which also drives thermal alerts and telemetry.
        self.scope_manager = ScopeManager(
            self.main_layout,
            self.settings,
            self._gpu_available,
            self._temp_available,
            lambda title, msg: NotificationService.notify(title, msg),
        )
        self.cpu_grid = self.scope_manager.build()

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

        self._apply_layout_mode(self.get_layout_mode())

    # ------------------------------------------------------------------
    # Scope visibility & layout density
    # ------------------------------------------------------------------
    def is_scope_visible(self, key: str) -> bool:
        """Return whether the given scope is currently shown (default True)."""
        return self.scope_manager.is_scope_visible(key)

    def set_scope_visible(self, key: str, visible: bool) -> None:
        """Persist and apply visibility for a single scope."""
        self.scope_manager.set_scope_visible(key, visible)

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
        self.scope_manager.on_theme_changed()
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
        self.scope_manager.apply_layout(scope_min_w)
        self.adjustSize()

    def show_snapshot_manager(self) -> None:
        """Open the process-snapshot manager dialog."""
        open_snapshot_manager(parent=self, on_clean=self._clean_using_snapshot)

    def show_cleanup_history(self) -> None:
        """Open the cleanup history dialog."""
        open_cleanup_history_dialog(parent=self)

    def show_app_overhead(self) -> None:
        """Open the app self-overhead dialog."""
        from ui.self_overhead_dialog import open_self_overhead_dialog
        open_self_overhead_dialog(parent=self)

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
        """Preview and kill only the extra processes that appeared after a snapshot."""
        self.cleanup_controller.clean_using_snapshot(snapshot)

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
        """Trigger resource release on a background thread; UI stays responsive."""
        self.cleanup_controller.request_release(aggressive=aggressive)

    def force_reclaim(self) -> None:
        """Run a full cleanup pass, bypassing the memory-pressure threshold."""
        self.cleanup_controller.force_reclaim()

    def preview_cleanup(self) -> None:
        """Scan + score without acting, then confirm before running."""
        self.cleanup_controller.preview_cleanup()

    def flush_standby_cache(self) -> None:
        """Purge the Windows standby file cache directly."""
        self.cleanup_controller.flush_standby_cache()

    def reset_throttled(self) -> None:
        """Restore processes throttled by a previous cleanup."""
        self.cleanup_controller.reset_throttled()

    def show_auto_clean_settings(self) -> None:
        """Open the auto-clean watchdog settings dialog."""
        from ui.auto_clean_settings_dialog import open_auto_clean_settings_dialog

        open_auto_clean_settings_dialog(
            self.settings, on_apply=self.reload_auto_clean_config, parent=self,
        )

    def reload_auto_clean_config(self) -> None:
        """Re-read auto-clean watchdog config from settings."""
        if self._auto_clean_watchdog is not None:
            self._auto_clean_watchdog.update_config(load_auto_clean_config(self.settings))

    def show_monitor_settings(self) -> None:
        """Open the unified monitor settings dialog."""
        from ui.monitor_settings_dialog import open_monitor_settings_dialog

        open_monitor_settings_dialog(
            self.settings, on_apply=self._reload_sensor_settings, parent=self,
        )

    def show_sensor_diagnostics(self) -> None:
        """Open the sensor diagnostics dialog."""
        from ui.sensor_diagnostics_dialog import open_sensor_diagnostics_dialog

        open_sensor_diagnostics_dialog(get_hub(), parent=self)

    def _reload_sensor_settings(self) -> None:
        """Re-read sensor source and telemetry/threshold settings after edits."""
        self.scope_manager.reload()
        get_hub().reload(str(self.settings.value("sensors/source", "auto")))

    def _restore_after_screenshot(self) -> None:
        self.capture_controller._restore_after_screenshot()

    def capture_regional(self) -> None:
        self.capture_controller.capture_regional()

    def capture_element(self) -> None:
        self.capture_controller.capture_element()

    def capture_last_region(self) -> None:
        self.capture_controller.capture_last_region()

    def capture_active_window(self) -> None:
        self.capture_controller.capture_active_window()

    def capture_scrolling(self) -> None:
        self.capture_controller.capture_scrolling()

    def capture_full_screen(self) -> None:
        self.capture_controller.capture_full_screen()

    def capture_full_desktop(self) -> None:
        self.capture_controller.capture_full_desktop()

    def pin_last_capture(self) -> None:
        self.capture_controller.pin_last_capture()

    def toggle_capture_collection(self) -> None:
        self.capture_controller.toggle_capture_collection()

    def paste_capture_collection(self) -> None:
        self.capture_controller.paste_capture_collection()

    def toggle_capture_toolbar(self) -> None:
        """Show or hide the floating one-click capture toolbar."""
        bar = self._capture_toolbar
        if bar is None:
            from ui.capture_toolbar import CaptureToolbar

            bar = CaptureToolbar()
            bar.region_requested.connect(self.capture_regional)
            bar.element_requested.connect(self.capture_element)
            bar.full_screen_requested.connect(self.capture_full_screen)
            bar.scrolling_requested.connect(self.capture_scrolling)
            bar.settings_requested.connect(self.show_screenshot_settings)
            self._capture_toolbar = bar
        if bar.isVisible():
            bar.hide()
            return
        bar.dock_near(self)
        bar.show()
        bar.raise_()

    def show_screenshot_settings(self) -> None:
        from ui.screenshot_settings_dialog import open_screenshot_settings_dialog

        open_screenshot_settings_dialog(
            self.settings,
            on_apply=self.capture_controller.reload_scroll_settings,
            parent=self,
        )

    def update_stats(self) -> None:
        """UI-thread housekeeping. Sampling happens off-thread in the sampler."""
        try:
            self._sync_microphone_recording_status()
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Unexpected error during stats update")

    def _render_snapshot(self, snap) -> None:
        """Render a SystemSnapshot — pure UI, no syscalls on this path."""
        try:
            watchdog = getattr(self, "_auto_clean_watchdog", None)
            if watchdog is not None:
                watchdog.observe(snap.ram_percent)
            self.scope_manager.update(
                list(snap.per_cpu),
                snap.cpu_avg,
                snap.ram_percent,
                snap.net_up_bps,
                snap.net_down_bps,
                snap.disk_rw_bps,
                snap.gpu_stats,
                snap.sensors,
            )
            if self._battery_available:
                self.battery_widget.update_stats(snap.battery)
            self.countdown_timer.tick()
        except RuntimeError:
            LOGGER.exception("Failed to render snapshot")

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
            self._apply_cadence()
        super().changeEvent(a0)

    def closeEvent(self, a0) -> None:  # noqa: N802  # pylint: disable=invalid-name
        """Cleanup shortcuts on close — ordered teardown via MonitorLifecycle."""
        if self._microphone_recorder.is_recording:
            try:
                self._microphone_recorder.stop_recording()
            except MicrophoneRecorderError:
                LOGGER.exception("Failed to stop microphone recording during shutdown")
        self.shortcut_service.unregister_all()
        self.app_chord_service.unregister_all()
        self.lifecycle.shutdown()
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
    # After the hub has had time to read, hint (once) when CPU/SSD temps are
    # blocked by lack of elevation but the backend is otherwise working.
    QTimer.singleShot(4000, _hint_elevation_if_needed)
    return app.exec()


def _hint_elevation_if_needed() -> None:
    """Notify once when temps need Administrator rights to read."""
    from services.win_elevation import is_elevated

    if is_elevated():
        return
    reading = get_hub().snapshot()
    backend_loaded = reading.backend_id != "none"
    temps_blocked = reading.cpu_temp_c is None and reading.ssd_temp_c is None
    if backend_loaded and temps_blocked:
        NotificationService.notify(
            APP_NAME,
            "Run as Administrator to read CPU, RAM, and SSD temperatures "
            "(GPU temperature works without it).",
        )


if __name__ == "__main__":
    sys.exit(main())

"""Context menu logic for TaskbarMonitor."""

import logging
import os
import sys
from typing import Protocol, runtime_checkable

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QAction, QActionGroup, QContextMenuEvent
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMenu,
    QSlider,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from core.config import (
    AUTOSTART_NAME,
    INTERVAL_OPTIONS,
    MAX_OPACITY,
    MIN_OPACITY,
    RUN_KEY_PATH,
    SLIDER_WIDTH,
    WINREG,
)
from ui.cleanup_menu import build_cleanup_menu
from ui.screenshot_menu import ScreenshotMonitor, add_screenshot_submenu

LOGGER = logging.getLogger(__name__)


@runtime_checkable
class MonitorProtocol(Protocol):
    """Protocol defining the interface for the taskbar monitor parent widget."""

    bg_opacity: int
    interval: int
    click_through: bool
    autohide_fullscreen: bool
    minimize_to_tray: bool
    settings: QSettings

    def update_opacity(self, value: int) -> None:
        """Set panel opacity."""

    def set_interval(self, milliseconds: int) -> None:
        """Set update interval."""

    def is_autostart_enabled(self) -> bool:
        """Check if autostart is enabled."""

    def toggle_autostart(self) -> None:
        """Toggle autostart status."""

    def set_click_through(self, enabled: bool) -> None:
        """Enable/disable click-through mode."""

    def set_autohide_fullscreen(self, enabled: bool) -> None:
        """Enable/disable auto-hide on fullscreen foreground apps."""

    def set_minimize_to_tray(self, enabled: bool) -> None:
        """Enable/disable minimize-to-tray behavior."""

    def show_processes_popup(self) -> None:
        """Open the top-processes popup."""

    def show_clipboard_popup(self) -> None:
        """Open the clipboard-history popup."""

    def show_snapshot_manager(self) -> None:
        """Open the process-snapshot manager dialog."""

    def show_cleanup_history(self) -> None:
        """Open the cleanup history dialog."""

    def show_cmdline_kill_dialog(self) -> None:
        """Open kill-by-WMI-command-line dialog."""

    def show_app_chord_manager(self) -> None:
        """Open the app chord shortcuts manager dialog."""

    def is_microphone_recording(self) -> bool:
        """Return whether microphone recording is active."""

    def toggle_microphone_recording(self) -> None:
        """Start or stop microphone recording."""

    def open_recordings_folder(self) -> None:
        """Open the configured recordings folder."""

    def show_recording_settings(self) -> None:
        """Open the microphone recording settings dialog."""

    def capture_regional(self) -> None:
        """Trigger regional screenshot."""

    def capture_element(self) -> None:
        """Trigger smart element screenshot."""

    def capture_last_region(self) -> None:
        """Trigger repeat regional screenshot."""

    def capture_active_window(self) -> None:
        """Trigger active window screenshot."""

    def capture_scrolling(self) -> None:
        """Trigger scrolling active window screenshot."""

    def capture_full_screen(self) -> None:
        """Trigger full-screen capture on the cursor's monitor."""

    def show_screenshot_settings(self) -> None:
        """Open screenshot output and scroll settings."""

    def reload_resource_profiles(self) -> None:
        """Reload smart/aggressive profile bindings from settings."""

    def force_reclaim(self) -> None:
        """Run a full cleanup pass, bypassing the pressure threshold."""

    def preview_cleanup(self) -> None:
        """Show a dry-run preview before running cleanup."""

    def flush_standby_cache(self) -> None:
        """Flush the Windows standby cache directly."""

    def reset_throttled(self) -> None:
        """Restore processes throttled by a previous cleanup."""

    def show_auto_clean_settings(self) -> None:
        """Open the auto-clean watchdog settings dialog."""

    def is_scope_visible(self, key: str) -> bool:
        """Return whether a scope is shown."""
        ...

    def set_scope_visible(self, key: str, visible: bool) -> None:
        """Toggle a scope's visibility."""

    def get_layout_mode(self) -> str:
        """Return the active layout density mode."""
        ...

    def set_layout_mode(self, mode: str) -> None:
        """Set the layout density mode."""

    def get_theme_mode(self) -> str:
        """Return the active theme mode (system/light/dark)."""
        ...

    def set_theme_mode(self, mode: str) -> None:
        """Set the active theme mode."""


class AppMenuBuilder:
    """Builds the context menu for the TaskbarMonitor."""

    @staticmethod
    def build_menu(parent: QWidget) -> QMenu:
        """Create and return the context menu."""
        if not isinstance(parent, MonitorProtocol):
            LOGGER.warning("Parent widget does not fully implement MonitorProtocol")

        menu = QMenu(parent)
        menu.setStyleSheet(
            """
            QMenu { background-color: #1a1a1a; color: white; border: 1px solid #333; padding: 5px; }
            QMenu::item:selected { background-color: #333; }
            QLabel { color: #aaa; font-size: 10px; padding: 0 5px; }
            """
        )

        # Opacity slider
        trans_action = QWidgetAction(parent)
        container = QWidget()
        container_layout = QVBoxLayout(container)
        container_layout.setContentsMargins(10, 5, 10, 5)
        label = QLabel("Background Opacity")
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(MIN_OPACITY, MAX_OPACITY)

        if isinstance(parent, MonitorProtocol):
            slider.setValue(parent.bg_opacity)
            slider.valueChanged.connect(parent.update_opacity)
        slider.setFixedWidth(SLIDER_WIDTH)

        container_layout.addWidget(label)
        container_layout.addWidget(slider)
        trans_action.setDefaultWidget(container)
        menu.addAction(trans_action)

        menu.addSeparator()

        # Interval menu
        interval_menu = menu.addMenu("Update Interval")
        for label_text, milliseconds in INTERVAL_OPTIONS:
            action = QAction(label_text, parent)
            action.setCheckable(True)
            if isinstance(parent, MonitorProtocol):
                action.setChecked(parent.interval == milliseconds)

            # Use a closure to capture milliseconds correctly
            def make_handler(ms_val: int):
                def handler(_checked: bool):
                    if isinstance(parent, MonitorProtocol):
                        parent.set_interval(ms_val)
                return handler

            action.triggered.connect(make_handler(milliseconds))
            interval_menu.addAction(action)

        menu.addSeparator()

        # Top-processes shortcut
        procs_action = QAction("Show Top Processes…", parent)
        if isinstance(parent, MonitorProtocol):
            procs_action.triggered.connect(parent.show_processes_popup)
        menu.addAction(procs_action)

        clipboard_action = QAction("Clipboard History…", parent)
        if isinstance(parent, MonitorProtocol):
            clipboard_action.triggered.connect(parent.show_clipboard_popup)
        menu.addAction(clipboard_action)

        snapshot_action = QAction("Process Snapshots…", parent)
        if isinstance(parent, MonitorProtocol):
            snapshot_action.triggered.connect(parent.show_snapshot_manager)
        menu.addAction(snapshot_action)

        cmdline_kill_action = QAction("Kill by Command Line (WMI)…", parent)
        if isinstance(parent, MonitorProtocol):
            cmdline_kill_action.triggered.connect(parent.show_cmdline_kill_dialog)
        menu.addAction(cmdline_kill_action)

        chord_action = QAction("App Chord Shortcuts…", parent)
        if isinstance(parent, MonitorProtocol):
            chord_action.triggered.connect(parent.show_app_chord_manager)
        menu.addAction(chord_action)

        if isinstance(parent, MonitorProtocol):
            AppMenuBuilder._add_recording_submenu(menu, parent)

        if isinstance(parent, ScreenshotMonitor):
            add_screenshot_submenu(menu, parent)

        cleanup_history_action = QAction("Cleanup History…", parent)
        if isinstance(parent, MonitorProtocol):
            cleanup_history_action.triggered.connect(parent.show_cleanup_history)
        menu.addAction(cleanup_history_action)

        # Resource cleanup submenu — quick actions + profile picker + settings
        if isinstance(parent, MonitorProtocol):
            build_cleanup_menu(menu, parent)

        # Graph visibility submenu
        if isinstance(parent, MonitorProtocol):
            AppMenuBuilder._add_graphs_submenu(menu, parent)

        # Layout density submenu
        if isinstance(parent, MonitorProtocol):
            AppMenuBuilder._add_layout_submenu(menu, parent)

        # Theme submenu
        if isinstance(parent, MonitorProtocol):
            AppMenuBuilder._add_theme_submenu(menu, parent)

        # Click-through toggle (Ctrl+Shift+Alt+C acts as an escape hatch)
        click_through_action = QAction("Click-Through Mode [Ctrl+Shift+Alt+C]", parent)
        click_through_action.setCheckable(True)
        if isinstance(parent, MonitorProtocol):
            click_through_action.setChecked(parent.click_through)
            click_through_action.toggled.connect(parent.set_click_through)
        menu.addAction(click_through_action)

        # Auto-hide on fullscreen toggle
        autohide_action = QAction("Auto-Hide on Fullscreen Apps", parent)
        autohide_action.setCheckable(True)
        if isinstance(parent, MonitorProtocol):
            autohide_action.setChecked(parent.autohide_fullscreen)
            autohide_action.toggled.connect(parent.set_autohide_fullscreen)
        menu.addAction(autohide_action)

        # Minimize-to-tray toggle
        tray_action = QAction("Minimize to Tray", parent)
        tray_action.setCheckable(True)
        if isinstance(parent, MonitorProtocol):
            tray_action.setChecked(parent.minimize_to_tray)
            tray_action.toggled.connect(parent.set_minimize_to_tray)
        menu.addAction(tray_action)

        menu.addSeparator()

        # Autostart toggle
        autostart_action = QAction("Auto Start with Windows", parent)
        autostart_action.setCheckable(True)
        if isinstance(parent, MonitorProtocol):
            autostart_action.setChecked(parent.is_autostart_enabled())
            autostart_action.triggered.connect(parent.toggle_autostart)
        menu.addAction(autostart_action)

        menu.addSeparator()

        # Exit action
        quit_action = QAction("Exit", parent)
        app = QApplication.instance()
        if app is not None:
            quit_action.triggered.connect(app.quit)
        menu.addAction(quit_action)

        return menu


    @staticmethod
    def _add_graphs_submenu(menu: QMenu, parent: "MonitorProtocol") -> None:
        """Submenu of checkable items to show/hide each scope graph."""
        widget_parent = parent if isinstance(parent, QWidget) else None
        graphs_menu = QMenu("Graphs", widget_parent)
        menu.addMenu(graphs_menu)
        labels = [
            ("cpu", "CPU"), ("ram", "RAM"), ("up", "Upload"), ("dn", "Download"),
            ("r/w", "Disk R/W"), ("gpu", "GPU"), ("vram", "VRAM"), ("temp", "Temp"),
        ]
        for key, label in labels:
            action = QAction(label, widget_parent)
            action.setCheckable(True)
            action.setChecked(parent.is_scope_visible(key))
            action.toggled.connect(_make_scope_toggler(parent, key))
            graphs_menu.addAction(action)

    @staticmethod
    def _add_layout_submenu(menu: QMenu, parent: "MonitorProtocol") -> None:
        """Submenu of layout density modes."""
        widget_parent = parent if isinstance(parent, QWidget) else None
        layout_menu = QMenu("Layout", widget_parent)
        menu.addMenu(layout_menu)
        active = parent.get_layout_mode()
        group = QActionGroup(layout_menu)
        group.setExclusive(True)
        for mode, label in (("compact", "Compact"), ("standard", "Standard"), ("roomy", "Roomy")):
            action = QAction(label, widget_parent)
            action.setCheckable(True)
            action.setChecked(mode == active)
            action.triggered.connect(_make_layout_picker(parent, mode))
            group.addAction(action)
            layout_menu.addAction(action)

    @staticmethod
    def _add_theme_submenu(menu: QMenu, parent: "MonitorProtocol") -> None:
        """Submenu of theme modes: System / Light / Dark."""
        widget_parent = parent if isinstance(parent, QWidget) else None
        theme_menu = QMenu("Theme", widget_parent)
        menu.addMenu(theme_menu)
        active = parent.get_theme_mode()
        group = QActionGroup(theme_menu)
        group.setExclusive(True)
        for mode, label in (("system", "System (auto)"), ("light", "Light"), ("dark", "Dark")):
            action = QAction(label, widget_parent)
            action.setCheckable(True)
            action.setChecked(mode == active)
            action.triggered.connect(_make_theme_picker(parent, mode))
            group.addAction(action)
            theme_menu.addAction(action)

    @staticmethod
    def _add_recording_submenu(menu: QMenu, parent: "MonitorProtocol") -> None:
        """Build the microphone-recording submenu."""
        widget_parent = parent if isinstance(parent, QWidget) else None
        recording_menu = QMenu("Microphone Recording", widget_parent)
        menu.addMenu(recording_menu)

        record_label = "Stop Recording" if parent.is_microphone_recording() else "Start Recording"
        record_action = QAction(record_label, widget_parent)
        record_action.triggered.connect(lambda _checked=False: parent.toggle_microphone_recording())
        recording_menu.addAction(record_action)

        open_folder_action = QAction("Open Recordings Folder", widget_parent)
        open_folder_action.triggered.connect(lambda _checked=False: parent.open_recordings_folder())
        recording_menu.addAction(open_folder_action)

        settings_action = QAction("Settings…", widget_parent)
        settings_action.triggered.connect(lambda _checked=False: parent.show_recording_settings())
        recording_menu.addAction(settings_action)


def _make_scope_toggler(parent: "MonitorProtocol", key: str):
    def handler(checked: bool) -> None:
        parent.set_scope_visible(key, checked)
    return handler


def _make_layout_picker(parent: "MonitorProtocol", mode: str):
    def handler(_checked: bool = False) -> None:
        parent.set_layout_mode(mode)
    return handler


def _make_theme_picker(parent: "MonitorProtocol", mode: str):
    def handler(_checked: bool = False) -> None:
        parent.set_theme_mode(mode)
    return handler


class ContextMenuHandler:
    """Handles the context menu event for the main widget."""

    def __init__(self, parent: QWidget):
        """Initialize with parent widget."""
        self.parent = parent

    def handle_event(self, a0: QContextMenuEvent) -> None:
        """Build and execute the menu at the event position."""
        menu = AppMenuBuilder.build_menu(self.parent)
        menu.exec(a0.globalPos())


class AutostartManager:
    """Manages Windows registry keys for autostart."""

    @staticmethod
    def is_enabled() -> bool:
        """Check if autostart is enabled in registry."""
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

    @staticmethod
    def toggle() -> None:
        """Toggle autostart entry in the registry."""
        if WINREG is None:
            return

        is_enabled = AutostartManager.is_enabled()
        if is_enabled:
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

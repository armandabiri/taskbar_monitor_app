"""Context menu assembly for TaskbarMonitor.

The reusable submenu sections live in ``ui.app_menu_sections``; the parent-widget
interface is ``ui.monitor_protocol.MonitorProtocol``; autostart lives in
``ui.autostart``. ``AutostartManager`` is re-exported here for backward
compatibility with existing importers.
"""

import logging

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QAction, QContextMenuEvent
from PyQt6.QtWidgets import (
    QApplication,
    QLabel,
    QMenu,
    QSlider,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from core.config import INTERVAL_OPTIONS, MAX_OPACITY, MIN_OPACITY, SLIDER_WIDTH
from ui.app_menu_sections import (
    add_graphs_submenu,
    add_layout_submenu,
    add_recording_submenu,
    add_theme_submenu,
)
from ui.autostart import AutostartManager  # re-exported facade
from ui.cleanup_menu import build_cleanup_menu
from ui.monitor_protocol import MonitorProtocol
from ui.screenshot_menu import ScreenshotMonitor, add_screenshot_submenu

LOGGER = logging.getLogger(__name__)

__all__ = ["AppMenuBuilder", "ContextMenuHandler", "AutostartManager"]


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
            add_recording_submenu(menu, parent)

        if isinstance(parent, ScreenshotMonitor):
            add_screenshot_submenu(menu, parent)

        cleanup_history_action = QAction("Cleanup History…", parent)
        if isinstance(parent, MonitorProtocol):
            cleanup_history_action.triggered.connect(parent.show_cleanup_history)
        menu.addAction(cleanup_history_action)

        app_overhead_action = QAction("App Footprint…", parent)
        if isinstance(parent, MonitorProtocol):
            app_overhead_action.triggered.connect(parent.show_app_overhead)
        menu.addAction(app_overhead_action)

        # Resource cleanup submenu — quick actions + profile picker + settings
        if isinstance(parent, MonitorProtocol):
            build_cleanup_menu(menu, parent)

        # Monitor settings + sensor diagnostics (units, sensor source, thresholds)
        monitor_settings_action = QAction("Monitor Settings…", parent)
        if isinstance(parent, MonitorProtocol):
            monitor_settings_action.triggered.connect(parent.show_monitor_settings)
        menu.addAction(monitor_settings_action)

        diagnostics_action = QAction("Sensor Diagnostics…", parent)
        if isinstance(parent, MonitorProtocol):
            diagnostics_action.triggered.connect(parent.show_sensor_diagnostics)
        menu.addAction(diagnostics_action)

        # Graph visibility, layout density, and theme submenus
        if isinstance(parent, MonitorProtocol):
            add_graphs_submenu(menu, parent)
            add_layout_submenu(menu, parent)
            add_theme_submenu(menu, parent)

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


class ContextMenuHandler:
    """Handles the context menu event for the main widget."""

    def __init__(self, parent: QWidget):
        """Initialize with parent widget."""
        self.parent = parent

    def handle_event(self, a0: QContextMenuEvent) -> None:
        """Build and execute the menu at the event position."""
        menu = AppMenuBuilder.build_menu(self.parent)
        menu.exec(a0.globalPos())

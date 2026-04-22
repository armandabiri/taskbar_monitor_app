"""Context menu logic for TaskbarMonitor."""

import logging
import os
import sys
from typing import Protocol, runtime_checkable

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

from core.config import (
    AUTOSTART_NAME,
    INTERVAL_OPTIONS,
    MAX_OPACITY,
    MIN_OPACITY,
    RUN_KEY_PATH,
    SLIDER_WIDTH,
    WINREG,
)

LOGGER = logging.getLogger(__name__)


@runtime_checkable
class MonitorProtocol(Protocol):
    """Protocol defining the interface for the taskbar monitor parent widget."""

    bg_opacity: int
    interval: int
    click_through: bool
    autohide_fullscreen: bool
    minimize_to_tray: bool

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

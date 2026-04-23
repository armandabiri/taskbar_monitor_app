"""System tray icon with minimize-to-tray support."""

from __future__ import annotations

import logging
import os
from typing import Callable

from PyQt6.QtGui import QAction, QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QSystemTrayIcon, QStyle, QWidget

LOGGER = logging.getLogger(__name__)


def build_tray(
    parent: QWidget,
    icon_path: str,
    on_toggle_visibility: Callable[[], None],
    on_release_smart: Callable[[], None],
    on_release_aggressive: Callable[[], None],
    on_show_processes: Callable[[], None],
    on_show_clipboard: Callable[[], None],
    get_click_through: Callable[[], bool],
    on_set_click_through: Callable[[bool], None],
) -> QSystemTrayIcon | None:
    """Create the system tray icon. Returns None if the platform has no tray."""
    if not QSystemTrayIcon.isSystemTrayAvailable():
        LOGGER.warning("System tray is not available on this platform")
        return None

    if os.path.exists(icon_path):
        icon = QIcon(icon_path)
    else:
        style = QApplication.style()
        icon = style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon) if style else QIcon()

    tray = QSystemTrayIcon(icon, parent)
    tray.setToolTip("Taskbar Monitor")

    menu = QMenu(parent)
    menu.setStyleSheet(
        "QMenu { background-color: #1a1a1a; color: white; border: 1px solid #333; padding: 4px; }"
        "QMenu::item:selected { background-color: #333; }"
    )

    act_toggle = QAction("Show / Hide Monitor", parent)
    act_toggle.triggered.connect(lambda: on_toggle_visibility())
    menu.addAction(act_toggle)

    menu.addSeparator()

    act_smart = QAction("AutoSmart Clear", parent)
    act_smart.triggered.connect(lambda: on_release_smart())
    menu.addAction(act_smart)

    act_aggr = QAction("Aggressive Clear", parent)
    act_aggr.triggered.connect(lambda: on_release_aggressive())
    menu.addAction(act_aggr)

    act_procs = QAction("Top Processes…", parent)
    act_procs.triggered.connect(lambda: on_show_processes())
    menu.addAction(act_procs)

    act_clipboard = QAction("Clipboard History…", parent)
    act_clipboard.triggered.connect(lambda: on_show_clipboard())
    menu.addAction(act_clipboard)

    menu.addSeparator()

    # Click-through toggle (critical escape hatch if click-through was set via the bar)
    act_click_through = QAction("Click-Through Mode [Ctrl+Shift+Alt+C]", parent)
    act_click_through.setCheckable(True)
    act_click_through.toggled.connect(lambda checked: on_set_click_through(checked))
    menu.addAction(act_click_through)

    menu.addSeparator()

    act_quit = QAction("Exit", parent)
    app = QApplication.instance()
    if app is not None:
        act_quit.triggered.connect(app.quit)
    menu.addAction(act_quit)

    # Sync the checkmark each time the menu opens so external toggles (hotkey,
    # bar context menu) are reflected here too.
    def _sync_state() -> None:
        act_click_through.blockSignals(True)
        act_click_through.setChecked(get_click_through())
        act_click_through.blockSignals(False)

    menu.aboutToShow.connect(_sync_state)

    tray.setContextMenu(menu)

    def _on_activated(reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            on_toggle_visibility()

    tray.activated.connect(_on_activated)
    tray.show()
    return tray

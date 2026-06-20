"""Reusable context-menu section builders (graphs, layout, theme, recording).

Extracted from ``menu_handler`` so the menu builder stays under the code-size
cap. Each function appends one submenu to the parent menu using the
``MonitorProtocol`` callbacks on ``parent``.
"""

from __future__ import annotations

from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QMenu, QWidget

# Scope visibility entries, including the dedicated GPU and SSD temperature scopes.
GRAPH_LABELS = (
    ("cpu", "CPU"), ("ram", "RAM"), ("up", "Upload"), ("dn", "Download"),
    ("r/w", "Disk R/W"), ("gpu", "GPU"), ("vram", "VRAM"),
    ("temp", "Temp (CPU/RAM)"), ("gputemp", "GPU Temp"), ("ssdtemp", "SSD Temp"),
)


def _make_scope_toggler(parent, key: str):
    def handler(checked: bool) -> None:
        parent.set_scope_visible(key, checked)
    return handler


def _make_layout_picker(parent, mode: str):
    def handler(_checked: bool = False) -> None:
        parent.set_layout_mode(mode)
    return handler


def _make_theme_picker(parent, mode: str):
    def handler(_checked: bool = False) -> None:
        parent.set_theme_mode(mode)
    return handler


def add_graphs_submenu(menu: QMenu, parent) -> None:
    """Submenu of checkable items to show/hide each scope graph."""
    widget_parent = parent if isinstance(parent, QWidget) else None
    graphs_menu = QMenu("Graphs", widget_parent)
    menu.addMenu(graphs_menu)
    for key, label in GRAPH_LABELS:
        action = QAction(label, widget_parent)
        action.setCheckable(True)
        action.setChecked(parent.is_scope_visible(key))
        action.toggled.connect(_make_scope_toggler(parent, key))
        graphs_menu.addAction(action)


def add_layout_submenu(menu: QMenu, parent) -> None:
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


def add_theme_submenu(menu: QMenu, parent) -> None:
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


def add_recording_submenu(menu: QMenu, parent) -> None:
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

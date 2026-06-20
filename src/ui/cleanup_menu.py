"""Builds the 'Resource Cleanup' context submenu.

Extracted from ``menu_handler`` so the menu module stays small and the new
cleanup actions (Force Reclaim, Preview, Flush standby, Reset throttled) live
next to the existing profile-picker submenu.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtGui import QAction, QActionGroup
from PyQt6.QtWidgets import QMenu, QWidget

from services.resource_control import (
    all_profiles,
    load_active_aggressive_profile,
    load_active_smart_profile,
    set_active_aggressive_profile,
    set_active_smart_profile,
    throttled_process_count,
)
from ui.resource_settings_dialog import open_resource_settings_dialog


def build_cleanup_menu(menu: QMenu, parent: Any) -> None:
    """Add the 'Resource Cleanup' submenu (actions + profile pickers) to ``menu``."""
    monitor: Any = parent
    widget_parent = parent if isinstance(parent, QWidget) else None
    cleanup_menu = QMenu("Resource Cleanup", widget_parent)
    menu.addMenu(cleanup_menu)

    _add_quick_actions(cleanup_menu, widget_parent, monitor)
    cleanup_menu.addSeparator()

    active_smart = load_active_smart_profile(monitor.settings).name
    active_aggressive = load_active_aggressive_profile(monitor.settings).name
    profiles = list(all_profiles(monitor.settings))

    _add_profile_radio_group(
        cleanup_menu, widget_parent,
        label="🧠  Smart button profile",
        profiles=profiles, active_name=active_smart,
        on_pick=lambda name: _activate_profile(monitor, name, aggressive=False),
    )
    cleanup_menu.addSeparator()
    _add_profile_radio_group(
        cleanup_menu, widget_parent,
        label="⚡  Aggressive button profile",
        profiles=profiles, active_name=active_aggressive,
        on_pick=lambda name: _activate_profile(monitor, name, aggressive=True),
    )
    cleanup_menu.addSeparator()

    settings_action = QAction("Settings…", widget_parent)
    settings_action.triggered.connect(
        lambda _checked=False: open_resource_settings_dialog(
            monitor.settings,
            on_apply=monitor.reload_resource_profiles,
            parent=widget_parent,
        )
    )
    cleanup_menu.addAction(settings_action)


def _add_quick_actions(cleanup_menu: QMenu, widget_parent: QWidget | None, parent: Any) -> None:
    force_action = QAction("⚡  Force Reclaim Now", widget_parent)
    force_action.setToolTip("Run a full cleanup pass, bypassing the memory-pressure threshold.")
    force_action.triggered.connect(lambda _checked=False: parent.force_reclaim())
    cleanup_menu.addAction(force_action)

    preview_action = QAction("🔍  Preview cleanup…", widget_parent)
    preview_action.setToolTip("Scan and score without acting; confirm before anything runs.")
    preview_action.triggered.connect(lambda _checked=False: parent.preview_cleanup())
    cleanup_menu.addAction(preview_action)

    flush_action = QAction("🧹  Flush standby cache", widget_parent)
    flush_action.setToolTip("Purge the Windows standby file cache to free RAM immediately.")
    flush_action.triggered.connect(lambda _checked=False: parent.flush_standby_cache())
    cleanup_menu.addAction(flush_action)

    pending = throttled_process_count()
    label = "↩  Reset throttled processes"
    if pending:
        label += f" ({pending})"
    reset_action = QAction(label, widget_parent)
    reset_action.setToolTip("Restore priority/affinity of processes throttled by cleanup.")
    reset_action.setEnabled(pending > 0)
    reset_action.triggered.connect(lambda _checked=False: parent.reset_throttled())
    cleanup_menu.addAction(reset_action)

    auto_action = QAction("🤖  Auto-clean settings…", widget_parent)
    auto_action.setToolTip("Automatically clean when RAM stays under pressure.")
    auto_action.triggered.connect(lambda _checked=False: parent.show_auto_clean_settings())
    cleanup_menu.addAction(auto_action)


def _add_profile_radio_group(
    cleanup_menu: QMenu,
    widget_parent: QWidget | None,
    *,
    label: str,
    profiles,
    active_name: str,
    on_pick,
) -> None:
    header = QAction(label, widget_parent)
    header.setEnabled(False)
    cleanup_menu.addAction(header)

    group = QActionGroup(cleanup_menu)
    group.setExclusive(True)
    for profile in profiles:
        action = QAction(f"  {profile.name}", widget_parent)
        action.setCheckable(True)
        action.setChecked(profile.name == active_name)
        action.triggered.connect(_make_picker(on_pick, profile.name))
        group.addAction(action)
        cleanup_menu.addAction(action)


def _make_picker(on_pick, profile_name: str):
    def handler(_checked: bool = False) -> None:
        on_pick(profile_name)
    return handler


def _activate_profile(parent: Any, profile_name: str, *, aggressive: bool) -> None:
    if aggressive:
        set_active_aggressive_profile(parent.settings, profile_name)
    else:
        set_active_smart_profile(parent.settings, profile_name)
    parent.reload_resource_profiles()

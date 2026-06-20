"""Screenshot submenu builder (extracted from menu_handler)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from PyQt6.QtWidgets import QMenu


@runtime_checkable
class ScreenshotMonitor(Protocol):
    """Capture entry points the screenshot submenu invokes on the monitor."""

    def capture_regional(self) -> None: ...
    def capture_element(self) -> None: ...
    def capture_last_region(self) -> None: ...
    def capture_full_screen(self) -> None: ...
    def capture_full_desktop(self) -> None: ...
    def capture_active_window(self) -> None: ...
    def capture_scrolling(self) -> None: ...
    def pin_last_capture(self) -> None: ...
    def toggle_capture_collection(self) -> None: ...
    def paste_capture_collection(self) -> None: ...
    def toggle_capture_toolbar(self) -> None: ...
    def show_screenshot_settings(self) -> None: ...


def _add(submenu: QMenu, label: str, handler) -> None:
    action = submenu.addAction(label)
    action.triggered.connect(lambda _checked=False: handler())


def add_screenshot_submenu(menu: QMenu, parent: "ScreenshotMonitor") -> None:
    """Build the screenshot submenu and attach it to ``menu``."""
    # Own the submenu via the parent menu so it (and its actions) survive
    # regardless of whether the monitor is a QWidget.
    submenu = QMenu("Screenshot", menu)
    menu.addMenu(submenu)

    _add(submenu, "Capture Region [Shift+Win+R]", parent.capture_regional)
    _add(submenu, "Capture Element (Smart) [Shift+Win+E]", parent.capture_element)
    _add(submenu, "Repeat Last Region Capture [Shift+Win+Alt+R]", parent.capture_last_region)
    _add(submenu, "Capture Full Screen [Shift+Win+F]", parent.capture_full_screen)
    _add(submenu, "Capture Whole Desktop [Shift+Win+D]", parent.capture_full_desktop)
    _add(submenu, "Capture Active Window [Shift+Win+W]", parent.capture_active_window)
    _add(submenu, "Capture Scrolling Window [Shift+Win+S]", parent.capture_scrolling)

    submenu.addSeparator()
    _add(submenu, "Pin Last Capture to Screen [Shift+Win+P]", parent.pin_last_capture)
    _add(submenu, "Start/Stop Capture Collection [Shift+Win+M]", parent.toggle_capture_collection)
    _add(submenu, "Paste Collected Captures [Shift+Win+V]", parent.paste_capture_collection)
    _add(submenu, "Show Capture Toolbar", parent.toggle_capture_toolbar)
    _add(submenu, "Screenshot Settings…", parent.show_screenshot_settings)

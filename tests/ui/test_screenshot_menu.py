from __future__ import annotations

from PyQt6.QtWidgets import QMenu

from ui.screenshot_menu import ScreenshotMonitor, add_screenshot_submenu


class _FakeMonitor:
    """Records which capture entry points the menu actions invoke."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def capture_regional(self) -> None:
        self.calls.append("regional")

    def capture_element(self) -> None:
        self.calls.append("element")

    def capture_last_region(self) -> None:
        self.calls.append("last_region")

    def capture_full_screen(self) -> None:
        self.calls.append("full_screen")

    def capture_full_desktop(self) -> None:
        self.calls.append("full_desktop")

    def capture_active_window(self) -> None:
        self.calls.append("active_window")

    def capture_scrolling(self) -> None:
        self.calls.append("scrolling")

    def pin_last_capture(self) -> None:
        self.calls.append("pin")

    def toggle_capture_collection(self) -> None:
        self.calls.append("collect")

    def paste_capture_collection(self) -> None:
        self.calls.append("paste")

    def toggle_capture_toolbar(self) -> None:
        self.calls.append("toolbar")

    def show_screenshot_settings(self) -> None:
        self.calls.append("settings")


def test_fake_monitor_satisfies_protocol() -> None:
    assert isinstance(_FakeMonitor(), ScreenshotMonitor)


def test_screenshot_submenu_builds_all_modes(qtbot) -> None:
    monitor = _FakeMonitor()
    menu = QMenu()
    qtbot.addWidget(menu)

    add_screenshot_submenu(menu, monitor)

    submenu = menu.actions()[0].menu()
    assert submenu is not None
    actions = [a for a in submenu.actions() if not a.isSeparator()]

    for action in actions:
        action.trigger()

    # Every mode, including the new ones (full desktop, pin, toolbar), is wired.
    assert monitor.calls == [
        "regional",
        "element",
        "last_region",
        "full_screen",
        "full_desktop",
        "active_window",
        "scrolling",
        "pin",
        "collect",
        "paste",
        "toolbar",
        "settings",
    ]

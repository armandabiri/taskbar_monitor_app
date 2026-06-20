from pathlib import Path

main = Path("src/main.py").read_text(encoding="utf-8").splitlines()
chunk = main[1061:1377]
out = []
for line in chunk:
    if line.startswith("    "):
        out.append("    " + line[4:])
    else:
        out.append(line)

header = '''"""Screenshot capture flows (menu, hotkeys, overlays)."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QPoint, QRect, QTimer
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication

from core.config import APP_NAME
from services.notification_service import NotificationService
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
from services.screenshot_settings import load_screenshot_settings, scroll_debug_dir

LOGGER = logging.getLogger(__name__)


class CaptureController:
    """Owns screenshot mode entry, scroll coordinator, and output routing."""

    def __init__(self, monitor) -> None:
        self._monitor = monitor
        shot = load_screenshot_settings(monitor.settings)
        self.scrolling_coordinator = ScrollingScreenshotCoordinator(
            monitor,
            scroll_delay_ms=shot.scroll_delay_ms,
            debug_dir=scroll_debug_dir(monitor.settings),
        )
        self.scrolling_coordinator.finished.connect(self._on_scrolling_capture_finished)
        self.scrolling_coordinator.failed.connect(self._on_scrolling_capture_failed)

'''

body = "\n".join(out)
replacements = [
    ("self.selectors", "self._monitor.selectors"),
    ("self.clipboard", "self._monitor.clipboard"),
    ("self.settings", "self._monitor.settings"),
    ("self.last_capture_rect", "self._monitor.last_capture_rect"),
    ("self.last_capture_screen_name", "self._monitor.last_capture_screen_name"),
    ("self.hide()", "self._monitor.hide()"),
    ("self.show()", "self._monitor.show()"),
    ("self.raise_()", "self._monitor.raise_()"),
    ("int(self.winId())", "int(self._monitor.winId())"),
    ("self._apply_win32_topmost()", "self._monitor._apply_win32_topmost()"),
]
for old, new in replacements:
    body = body.replace(old, new)

Path("src/ui/capture_controller.py").write_text(header + body + "\n", encoding="utf-8")
print("lines", len((header + body).splitlines()))

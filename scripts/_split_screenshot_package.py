"""One-off helper to split screenshot_service.py into the screenshot package."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "services"
SERVICE = SRC / "screenshot_service.py"
ORIGINAL = ROOT / "scripts" / "_screenshot_service_original.py"
PKG = SRC / "screenshot"


def slice_lines(lines: list[str], start: int, end: int) -> str:
    """1-based inclusive start/end."""
    return "\n".join(lines[start - 1 : end]) + "\n"


def main() -> None:
    lines = ORIGINAL.read_text(encoding="utf-8").splitlines()
    PKG.mkdir(parents=True, exist_ok=True)

    win32_common_header = '''"""Win32 ctypes helpers shared by screenshot capture and scroll input."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

LOGGER = logging.getLogger(__name__)

'''

    win32_common_body = slice_lines(lines, 20, 91) + slice_lines(lines, 119, 137)
    (PKG / "win32_common.py").write_text(win32_common_header + win32_common_body, encoding="utf-8")

    window_geometry_header = '''"""Window bounds, monitor mapping, and Qt/native coordinate conversion."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QGuiApplication, QScreen

from services import uia_service
from services.screenshot.win32_common import (
    LOGGER,
    _GA_ROOT,
    _POINT,
    _SW_RESTORE,
    _get_kernel32,
    _get_user32,
)

'''

    window_geometry_body = slice_lines(lines, 94, 108) + slice_lines(lines, 140, 473)
    (PKG / "window_geometry.py").write_text(
        window_geometry_header + window_geometry_body, encoding="utf-8"
    )

    window_targets_header = '''"""Window hit-testing, selection, and focus helpers for capture."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from dataclasses import dataclass

from PyQt6.QtCore import QPoint

from services.screenshot.win32_common import (
    LOGGER,
    _GA_ROOT,
    _POINT,
    _SW_RESTORE,
    _get_kernel32,
    _get_user32,
)
from services.screenshot.window_geometry import (
    get_window_client_rect,
    get_window_rect_dwm,
    hwnd_client_screen_local_rect,
    native_rect_to_screen_local_rect,
    qt_global_point_to_native,
)

'''

    window_targets_body = slice_lines(lines, 111, 117) + slice_lines(lines, 475, 650)
    (PKG / "window_targets.py").write_text(
        window_targets_header + window_targets_body, encoding="utf-8"
    )

    win32_gdi_header = '''"""Low-level GDI desktop rectangle capture."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PyQt6.QtGui import QImage

from services.screenshot.win32_common import (
    LOGGER,
    _BI_RGB,
    _BITMAPINFO,
    _BITMAPINFOHEADER,
    _CAPTUREBLT,
    _DIB_RGB_COLORS,
    _SRCCOPY,
    _get_gdi32,
    _get_user32,
)

'''

    win32_gdi_body = slice_lines(lines, 652, 774)
    (PKG / "win32_gdi.py").write_text(win32_gdi_header + win32_gdi_body, encoding="utf-8")

    win32_capture_header = '''"""GDI / PrintWindow screen and window capture."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap, QScreen

from services.screenshot.win32_common import (
    LOGGER,
    _BI_RGB,
    _BITMAPINFO,
    _BITMAPINFOHEADER,
    _DIB_RGB_COLORS,
    _PW_RENDERFULLCONTENT,
    _get_gdi32,
    _get_user32,
)
from services.screenshot.win32_gdi import grab_native_rect
from services.screenshot.window_geometry import (
    get_window_rect_dwm,
    qt_screen_local_rect_to_native,
)

'''

    win32_capture_body = slice_lines(lines, 777, 1004)
    (PKG / "win32_capture.py").write_text(
        win32_capture_header + win32_capture_body, encoding="utf-8"
    )
    # Rename _grab_native_rect calls to public grab_native_rect from win32_gdi.
    capture_path = PKG / "win32_capture.py"
    capture_text = capture_path.read_text(encoding="utf-8")
    capture_text = capture_text.replace("_grab_native_rect(", "grab_native_rect(")
    capture_path.write_text(capture_text, encoding="utf-8")

    gdi_path = PKG / "win32_gdi.py"
    gdi_text = gdi_path.read_text(encoding="utf-8")
    gdi_text = gdi_text.replace("def _grab_native_rect(", "def grab_native_rect(")
    gdi_path.write_text(gdi_text, encoding="utf-8")

    scroll_input_header = '''"""Synthetic mouse wheel and click input for scroll capture."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

from services.screenshot.win32_common import (
    LOGGER,
    _INPUT,
    _INPUT_MOUSE,
    _MOUSEEVENTF_LEFTDOWN,
    _MOUSEEVENTF_LEFTUP,
    _MOUSEEVENTF_WHEEL,
    _MOUSEINPUT,
    _InputUnion,
    _ULONG_PTR,
    _WHEEL_DELTA,
    _WM_MOUSEWHEEL,
    _get_user32,
)

'''

    scroll_input_body = slice_lines(lines, 1006, 1144)
    (PKG / "scroll_input.py").write_text(scroll_input_header + scroll_input_body, encoding="utf-8")

    stitch_header = '''"""Image similarity and vertical stitch alignment."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QRect
from PyQt6.QtGui import QImage, QPainter

LOGGER = logging.getLogger(__name__)

'''

    stitch_body = slice_lines(lines, 1146, 1318)
    (PKG / "stitch_alignment.py").write_text(stitch_header + stitch_body, encoding="utf-8")

    coord_imports = """
from services.screenshot.scroll_input import (
    _click_native_point,
    _post_wheel_scroll,
    _send_wheel_scroll_at,
)
from services.screenshot.window_targets import (
    _current_cursor_native_point,
    _focus_child_window,
    _force_foreground,
)
from services.screenshot.stitch_alignment import (
    _stride_notches,
    are_images_similar,
    find_vertical_offset,
    stitch_images,
)
from services.screenshot.win32_capture import grab_screen_region, grab_window_pixmap
from services.screenshot.win32_common import _get_user32
from services.screenshot.window_targets import (
    get_foreground_window,
    is_valid_capture_window,
)
from services.screenshot.window_geometry import (
    hwnd_client_screen_local_rect,
    native_rect_to_screen_local_rect,
    screen_local_rect_center_to_global,
    screen_local_rect_center_to_native,
)
"""

    scroll_core_body = slice_lines(lines, 1321, 1526)
    phases_header = f'''"""Scroll coordinator phase handlers (capture loop and stitching)."""

from __future__ import annotations

import json
import logging
import os

from PyQt6.QtCore import QRect, QSize
from PyQt6.QtGui import QImage

from services import uia_service
{coord_imports}

LOGGER = logging.getLogger(__name__)


class ScrollCoordinatorPhases:
'''

    phases_body = slice_lines(lines, 1528, 1860)
    phases_text = phases_header + phases_body
    # Indent phase methods — they were class methods in the original file.
    phase_lines: list[str] = []
    for line in phases_text.splitlines():
        if line.startswith("    def ") or line.startswith("        ") or line.startswith("    "):
            phase_lines.append(line)
        else:
            phase_lines.append(line)
    (PKG / "scroll_coordinator_phases.py").write_text("\n".join(phase_lines) + "\n", encoding="utf-8")

    scroll_header = f'''"""Timed scrolling screenshot orchestration."""

from __future__ import annotations

import logging

from PyQt6.QtCore import QObject, QRect, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QImage, QScreen
from PyQt6.QtWidgets import QApplication

from services import uia_service
from services.screenshot.scroll_coordinator_phases import ScrollCoordinatorPhases
from services.screenshot.win32_capture import grab_screen_region, grab_window_pixmap
from services.screenshot.win32_common import _get_user32
from services.screenshot.window_geometry import (
    hwnd_client_screen_local_rect,
    native_rect_to_screen_local_rect,
    screen_local_rect_center_to_global,
    screen_local_rect_center_to_native,
)
from services.screenshot.window_targets import (
    _current_cursor_native_point,
    get_foreground_window,
    is_valid_capture_window,
)

LOGGER = logging.getLogger(__name__)


class ScrollingScreenshotCoordinator(ScrollCoordinatorPhases, QObject):
'''

    scroll_body = scroll_core_body.replace("class ScrollingScreenshotCoordinator(QObject):", "", 1)
    (PKG / "scroll_coordinator.py").write_text(scroll_header + scroll_body, encoding="utf-8")

    init_py = '''"""Screenshot capture package (Win32 capture, stitch, scroll coordinator)."""

from services.screenshot.scroll_coordinator import ScrollingScreenshotCoordinator
from services.screenshot.stitch_alignment import (
    are_images_similar,
    find_vertical_offset,
    stitch_images,
)
from services.screenshot.win32_capture import (
    crop_screen_snapshot,
    grab_screen_region,
    grab_screen_snapshot,
    grab_window_pixmap,
)
from services.screenshot.window_geometry import (
    element_rects_for_screen,
    get_window_client_rect,
    get_window_rect_dwm,
    hwnd_client_screen_local_rect,
    native_rect_to_screen_local_rect,
    qt_global_point_to_native,
    qt_screen_local_rect_to_native,
    screen_at_qt_point,
    screen_local_rect_center_to_global,
    screen_local_rect_center_to_native,
)
from services.screenshot.window_targets import (
    WindowSelection,
    get_foreground_window,
    is_valid_capture_window,
    window_from_qt_point,
    window_selections_from_qt_point,
)

__all__ = [
    "ScrollingScreenshotCoordinator",
    "WindowSelection",
    "are_images_similar",
    "crop_screen_snapshot",
    "element_rects_for_screen",
    "find_vertical_offset",
    "get_foreground_window",
    "grab_screen_region",
    "grab_screen_snapshot",
    "grab_window_pixmap",
    "hwnd_client_screen_local_rect",
    "is_valid_capture_window",
    "native_rect_to_screen_local_rect",
    "qt_global_point_to_native",
    "qt_screen_local_rect_to_native",
    "screen_at_qt_point",
    "screen_local_rect_center_to_global",
    "screen_local_rect_center_to_native",
    "stitch_images",
    "window_from_qt_point",
    "window_selections_from_qt_point",
]
'''
    (PKG / "__init__.py").write_text(init_py, encoding="utf-8")

    facade = '''"""Screenshot helpers and scrolling capture coordinator (facade)."""

from __future__ import annotations

from services.screenshot.scroll_coordinator import ScrollingScreenshotCoordinator
from services.screenshot.scroll_input import _pack_signed_words
from services.screenshot.stitch_alignment import (
    _offset_fallback,
    _stride_notches,
    are_images_similar,
    find_vertical_offset,
    stitch_images,
)
from services.screenshot.win32_capture import (
    crop_screen_snapshot,
    grab_screen_region,
    grab_screen_snapshot,
    grab_window_pixmap,
)
from services.screenshot.window_geometry import (
    element_rects_for_screen,
    get_window_client_rect,
    get_window_rect_dwm,
    hwnd_client_screen_local_rect,
    native_rect_to_screen_local_rect,
    qt_global_point_to_native,
    qt_screen_local_rect_to_native,
    screen_at_qt_point,
    screen_local_rect_center_to_global,
    screen_local_rect_center_to_native,
)
from services.screenshot.window_targets import (
    WindowSelection,
    get_foreground_window,
    is_valid_capture_window,
    window_from_qt_point,
    window_selections_from_qt_point,
)

__all__ = [
    "ScrollingScreenshotCoordinator",
    "WindowSelection",
    "_offset_fallback",
    "_pack_signed_words",
    "_stride_notches",
    "are_images_similar",
    "crop_screen_snapshot",
    "element_rects_for_screen",
    "find_vertical_offset",
    "get_foreground_window",
    "get_window_client_rect",
    "get_window_rect_dwm",
    "grab_screen_region",
    "grab_screen_snapshot",
    "grab_window_pixmap",
    "hwnd_client_screen_local_rect",
    "is_valid_capture_window",
    "native_rect_to_screen_local_rect",
    "qt_global_point_to_native",
    "qt_screen_local_rect_to_native",
    "screen_at_qt_point",
    "screen_local_rect_center_to_global",
    "screen_local_rect_center_to_native",
    "stitch_images",
    "window_from_qt_point",
    "window_selections_from_qt_point",
]
'''
    SERVICE.write_text(facade, encoding="utf-8")

    for name in [
        "win32_common.py",
        "win32_gdi.py",
        "window_geometry.py",
        "window_targets.py",
        "win32_capture.py",
        "scroll_input.py",
        "stitch_alignment.py",
        "scroll_coordinator_phases.py",
        "scroll_coordinator.py",
        "screenshot_service.py",
    ]:
        path = PKG / name if name != "screenshot_service.py" else SERVICE
        count = len(path.read_text(encoding="utf-8").splitlines())
        print(f"{path.relative_to(ROOT)}: {count} lines")


if __name__ == "__main__":
    main()

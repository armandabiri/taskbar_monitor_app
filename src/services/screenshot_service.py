"""Screenshot helpers and scrolling capture coordinator (facade)."""

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
    grab_virtual_desktop,
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
    "grab_virtual_desktop",
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

"""Window bounds, monitor mapping, and Qt/native coordinate conversion."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QGuiApplication, QScreen

from services import uia_service
from services.screenshot.win32_common import _POINT, LOGGER, _get_user32
from services.screenshot.window_monitors import (
    _native_monitor_for_screen,
    _screen_for_native_rect,
)


def get_window_rect_dwm(hwnd: int) -> wintypes.RECT | None:
    """Get the visible native-pixel bounds of a window."""
    rect = wintypes.RECT()
    try:
        ctypes.windll.dwmapi.DwmGetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        hr = ctypes.windll.dwmapi.DwmGetWindowAttribute(
            hwnd,
            9,  # DWMWA_EXTENDED_FRAME_BOUNDS
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0:
            return rect
    except (AttributeError, OSError) as exc:
        LOGGER.debug("DwmGetWindowAttribute failed: %s", exc)

    user32 = _get_user32()
    if user32 is None:
        return None
    try:
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        if user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return rect
    except OSError as exc:
        LOGGER.debug("GetWindowRect failed: %s", exc)

    return None


def get_window_client_rect(hwnd: int) -> wintypes.RECT | None:
    """Get a window's client area in native screen pixels."""
    user32 = _get_user32()
    if user32 is None or not hwnd:
        return None
    try:
        user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetClientRect.restype = wintypes.BOOL
        user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(_POINT)]
        user32.ClientToScreen.restype = wintypes.BOOL

        client_rect = wintypes.RECT()
        if not user32.GetClientRect(hwnd, ctypes.byref(client_rect)):
            return None
        top_left = _POINT(client_rect.left, client_rect.top)
        bottom_right = _POINT(client_rect.right, client_rect.bottom)
        if not user32.ClientToScreen(hwnd, ctypes.byref(top_left)):
            return None
        if not user32.ClientToScreen(hwnd, ctypes.byref(bottom_right)):
            return None
        return wintypes.RECT(
            int(top_left.x),
            int(top_left.y),
            int(bottom_right.x),
            int(bottom_right.y),
        )
    except OSError as exc:
        LOGGER.debug("GetClientRect/ClientToScreen failed: %s", exc)
        return None


def native_rect_to_screen_local_rect(rect: wintypes.RECT) -> tuple[QScreen, QRect] | None:
    """Map a native screen-pixel rectangle to a screen-local Qt logical rectangle."""
    match = _screen_for_native_rect(rect)
    if match is None:
        return None

    screen, monitor = match
    screen_geom = screen.geometry()
    scale_x = screen_geom.width() / max(1, monitor.width)
    scale_y = screen_geom.height() / max(1, monitor.height)
    local_rect = QRect(
        round((int(rect.left) - monitor.left) * scale_x),
        round((int(rect.top) - monitor.top) * scale_y),
        max(1, round((int(rect.right) - int(rect.left)) * scale_x)),
        max(1, round((int(rect.bottom) - int(rect.top)) * scale_y)),
    ).intersected(QRect(QPoint(0, 0), screen_geom.size()))
    if local_rect.isEmpty():
        return None
    return (screen, local_rect)


def element_rects_for_screen(
    screen: QScreen,
    native_rects: list[uia_service.ElementRect] | None = None,
) -> list[tuple[QRect, str]]:
    """Map UIA element rectangles onto one screen as (local logical rect, label).

    Returns rectangles sorted largest-first so a hover hit-test can pick the
    smallest element containing the cursor. Pass ``native_rects`` to reuse a
    single tree walk across screens. Empty when UIA is unavailable.
    """
    if native_rects is None:
        native_rects = uia_service.collect_element_rects()
    if not native_rects:
        return []

    monitor = _native_monitor_for_screen(screen)
    geom = screen.geometry()
    screen_rect = QRect(QPoint(0, 0), geom.size())
    if monitor is not None and monitor.width > 0 and monitor.height > 0:
        scale_x = geom.width() / monitor.width
        scale_y = geom.height() / monitor.height
        origin_x, origin_y = monitor.left, monitor.top
        bounds = (monitor.left, monitor.top, monitor.right, monitor.bottom)
    else:
        ratio = screen.devicePixelRatio() or 1.0
        scale_x = scale_y = 1.0 / ratio
        origin_x = round(geom.x() * ratio)
        origin_y = round(geom.y() * ratio)
        bounds = (
            origin_x,
            origin_y,
            origin_x + round(geom.width() * ratio),
            origin_y + round(geom.height() * ratio),
        )

    mapped: list[tuple[QRect, str]] = []
    for native in native_rects:
        center_x = (native.left + native.right) // 2
        center_y = (native.top + native.bottom) // 2
        if not (bounds[0] <= center_x < bounds[2] and bounds[1] <= center_y < bounds[3]):
            continue
        local = QRect(
            round((native.left - origin_x) * scale_x),
            round((native.top - origin_y) * scale_y),
            max(1, round(native.width * scale_x)),
            max(1, round(native.height * scale_y)),
        ).intersected(screen_rect)
        if local.width() < 4 or local.height() < 4:
            continue
        label = native.name or native.control_type
        mapped.append((local, label))

    mapped.sort(key=lambda item: item[0].width() * item[0].height(), reverse=True)
    return mapped


def hwnd_client_screen_local_rect(hwnd: int) -> tuple[QScreen, QRect] | None:
    """Return the HWND client area as a screen-local Qt logical rectangle."""
    rect = get_window_client_rect(hwnd) or get_window_rect_dwm(hwnd)
    if rect is None:
        return None
    return native_rect_to_screen_local_rect(rect)


def screen_at_qt_point(point: QPoint) -> QScreen | None:
    """Find the Qt screen that contains a global Qt logical point."""
    screen = QGuiApplication.screenAt(point)
    if screen is not None:
        return screen
    for candidate in QGuiApplication.screens():
        if candidate.geometry().contains(point):
            return candidate
    return QGuiApplication.primaryScreen()


def qt_screen_local_rect_to_native(screen: QScreen, rect: QRect) -> tuple[int, int, int, int]:
    """Map a screen-local Qt logical rectangle to native physical coordinates."""
    normalized = QRect(rect).normalized().intersected(QRect(QPoint(0, 0), screen.geometry().size()))
    if normalized.isEmpty():
        return (0, 0, 0, 0)

    monitor = _native_monitor_for_screen(screen)
    if monitor is None:
        ratio = screen.devicePixelRatio()
        geom = screen.geometry()
        return (
            int((geom.x() + normalized.x()) * ratio),
            int((geom.y() + normalized.y()) * ratio),
            max(1, int(normalized.width() * ratio)),
            max(1, int(normalized.height() * ratio)),
        )

    geom = screen.geometry()
    scale_x = monitor.width / max(1, geom.width())
    scale_y = monitor.height / max(1, geom.height())
    x = monitor.left + round(normalized.x() * scale_x)
    y = monitor.top + round(normalized.y() * scale_y)
    width = max(1, round(normalized.width() * scale_x))
    height = max(1, round(normalized.height() * scale_y))
    return (x, y, width, height)


def qt_global_point_to_native(point: QPoint) -> tuple[int, int]:
    """Map a global Qt logical point to native physical coordinates."""
    screen = screen_at_qt_point(point)
    if screen is None:
        return (point.x(), point.y())

    local = point - screen.geometry().topLeft()
    x, y, _, _ = qt_screen_local_rect_to_native(screen, QRect(local.x(), local.y(), 1, 1))
    return (x, y)


def screen_local_rect_center_to_native(screen: QScreen, rect: QRect) -> tuple[int, int]:
    """Return a screen-local Qt logical rectangle center in native screen pixels."""
    normalized = QRect(rect).normalized()
    center = normalized.center()
    x, y, _, _ = qt_screen_local_rect_to_native(screen, QRect(center.x(), center.y(), 1, 1))
    return (x, y)


def screen_local_rect_center_to_global(screen: QScreen, rect: QRect) -> tuple[int, int]:
    """Return a screen-local Qt logical rectangle center in Qt global coordinates."""
    normalized = QRect(rect).normalized()
    center = normalized.center() + screen.geometry().topLeft()
    return (center.x(), center.y())


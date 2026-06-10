"""Screenshot helpers and scrolling capture coordinator."""

from __future__ import annotations

import ctypes
import json
import logging
import os
from ctypes import wintypes
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QPoint, QRect, QSize, QTimer, pyqtSignal
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QPixmap, QScreen
from PyQt6.QtWidgets import QApplication

LOGGER = logging.getLogger(__name__)

_CAPTUREBLT = 0x40000000
_SRCCOPY = 0x00CC0020
_DIB_RGB_COLORS = 0
_BI_RGB = 0
_GA_ROOT = 2
_SW_RESTORE = 9
_WM_MOUSEWHEEL = 0x020A
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_WHEEL_DELTA = 120
_INPUT_MOUSE = 0
_ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class _RGBQUAD(ctypes.Structure):
    _fields_ = [
        ("rgbBlue", ctypes.c_ubyte),
        ("rgbGreen", ctypes.c_ubyte),
        ("rgbRed", ctypes.c_ubyte),
        ("rgbReserved", ctypes.c_ubyte),
    ]


class _BITMAPINFO(ctypes.Structure):
    _fields_ = [
        ("bmiHeader", _BITMAPINFOHEADER),
        ("bmiColors", _RGBQUAD * 1),
    ]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", _ULONG_PTR),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("union", _InputUnion),
    ]


@dataclass(frozen=True)
class _NativeMonitor:
    device: str
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top


@dataclass(frozen=True)
class WindowSelection:
    """Window handles resolved from a scroll-selector click."""

    capture_hwnd: int
    scroll_hwnd: int


def _get_user32():
    try:
        return ctypes.windll.user32
    except AttributeError:
        return None


def _get_gdi32():
    try:
        return ctypes.windll.gdi32
    except AttributeError:
        return None


def _get_kernel32():
    try:
        return ctypes.windll.kernel32
    except AttributeError:
        return None


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


def _monitor_name_key(name: str) -> str:
    return name.replace("\\\\.\\", "").strip().lower()


def _native_monitors() -> list[_NativeMonitor]:
    """Return native monitor rectangles in physical pixels."""
    user32 = _get_user32()
    if user32 is None or not hasattr(ctypes, "WINFUNCTYPE"):
        return []

    class _MONITORINFOEXW(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", wintypes.RECT),
            ("rcWork", wintypes.RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    monitors: list[_NativeMonitor] = []
    monitor_enum_proc = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(wintypes.RECT),
        wintypes.LPARAM,
    )

    def callback(hmonitor, _hdc, _rect, _lparam) -> bool:
        info = _MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(info)
        if user32.GetMonitorInfoW(hmonitor, ctypes.byref(info)):
            rect = info.rcMonitor
            monitors.append(
                _NativeMonitor(
                    device=info.szDevice,
                    left=int(rect.left),
                    top=int(rect.top),
                    right=int(rect.right),
                    bottom=int(rect.bottom),
                )
            )
        return True

    try:
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.c_void_p]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.EnumDisplayMonitors.restype = wintypes.BOOL
        user32.EnumDisplayMonitors(0, 0, monitor_enum_proc(callback), 0)
    except OSError as exc:
        LOGGER.debug("EnumDisplayMonitors failed: %s", exc)
        return []
    return monitors


def _native_monitor_for_screen(screen: QScreen) -> _NativeMonitor | None:
    monitors = _native_monitors()
    if not monitors:
        return None

    geom = screen.geometry()
    expected_width = round(geom.width() * screen.devicePixelRatio())
    expected_height = round(geom.height() * screen.devicePixelRatio())
    positioned: list[tuple[int, _NativeMonitor]] = []
    for monitor in monitors:
        position_delta = abs(monitor.left - geom.x()) + abs(monitor.top - geom.y())
        size_delta = abs(monitor.width - expected_width) + abs(monitor.height - expected_height)
        positioned.append((position_delta * 10 + size_delta, monitor))
    if positioned:
        best_score, best_monitor = min(positioned, key=lambda item: item[0])
        if best_score < 500:
            return best_monitor

    wanted = _monitor_name_key(screen.name())
    for monitor in monitors:
        if _monitor_name_key(monitor.device) == wanted:
            return monitor

    screens = QGuiApplication.screens()
    try:
        index = screens.index(screen)
    except ValueError:
        return None
    if index < len(monitors):
        return monitors[index]
    return None


def _screen_for_native_rect(rect: wintypes.RECT) -> tuple[QScreen, _NativeMonitor] | None:
    left = int(rect.left)
    top = int(rect.top)
    right = int(rect.right)
    bottom = int(rect.bottom)
    if right <= left or bottom <= top:
        return None

    best: tuple[int, QScreen, _NativeMonitor] | None = None
    for screen in QGuiApplication.screens():
        monitor = _native_monitor_for_screen(screen)
        if monitor is None:
            continue
        overlap_w = max(0, min(right, monitor.right) - max(left, monitor.left))
        overlap_h = max(0, min(bottom, monitor.bottom) - max(top, monitor.top))
        area = overlap_w * overlap_h
        if area > 0 and (best is None or area > best[0]):
            best = (area, screen, monitor)
    if best is not None:
        return (best[1], best[2])

    center_x = left + (right - left) // 2
    center_y = top + (bottom - top) // 2
    for screen in QGuiApplication.screens():
        monitor = _native_monitor_for_screen(screen)
        if monitor is None:
            continue
        if monitor.left <= center_x < monitor.right and monitor.top <= center_y < monitor.bottom:
            return (screen, monitor)
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


def _window_selection_from_native_point(x: int, y: int) -> WindowSelection | None:
    user32 = _get_user32()
    if user32 is None:
        return None
    try:
        user32.WindowFromPoint.argtypes = [_POINT]
        user32.WindowFromPoint.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        hwnd = int(user32.WindowFromPoint(_POINT(x, y)))
        if not hwnd:
            return None
        root = int(user32.GetAncestor(hwnd, _GA_ROOT))
        return WindowSelection(capture_hwnd=root or hwnd, scroll_hwnd=hwnd)
    except OSError as exc:
        LOGGER.debug("WindowFromPoint failed: %s", exc)
        return None


def _current_cursor_native_point() -> tuple[int, int] | None:
    user32 = _get_user32()
    if user32 is None:
        return None
    try:
        user32.GetCursorPos.argtypes = [ctypes.POINTER(_POINT)]
        user32.GetCursorPos.restype = wintypes.BOOL
        point = _POINT()
        if user32.GetCursorPos(ctypes.byref(point)):
            return (int(point.x), int(point.y))
    except OSError as exc:
        LOGGER.debug("GetCursorPos failed: %s", exc)
    return None


def window_selections_from_qt_point(point: QPoint) -> list[WindowSelection]:
    """Return possible window selections under a Qt global point.

    Qt global mouse coordinates can be logical or native depending on the DPI
    awareness path in use, so probe both interpretations and de-duplicate.
    """
    candidates = [(point.x(), point.y()), qt_global_point_to_native(point)]
    cursor_point = _current_cursor_native_point()
    if cursor_point is not None:
        candidates.append(cursor_point)

    selections: list[WindowSelection] = []
    seen: set[tuple[int, int]] = set()
    for x, y in candidates:
        selection = _window_selection_from_native_point(x, y)
        if selection is None:
            continue
        key = (selection.capture_hwnd, selection.scroll_hwnd)
        if key in seen:
            continue
        seen.add(key)
        selections.append(selection)
    return selections


def window_from_qt_point(point: QPoint) -> int:
    """Return the root HWND under a Qt global point."""
    selections = window_selections_from_qt_point(point)
    return selections[0].capture_hwnd if selections else 0


def is_valid_capture_window(hwnd: int, own_hwnd: int = 0) -> bool:
    """Return True when an HWND is suitable for window capture."""
    if not hwnd:
        return False
    user32 = _get_user32()
    if user32 is None:
        return False
    try:
        user32.GetShellWindow.restype = wintypes.HWND
        user32.GetDesktopWindow.restype = wintypes.HWND
        user32.IsWindowVisible.argtypes = [wintypes.HWND]
        user32.IsWindowVisible.restype = wintypes.BOOL
        if hwnd in (int(user32.GetShellWindow()), int(user32.GetDesktopWindow())):
            return False
        if own_hwnd and hwnd == own_hwnd:
            return False
        if not user32.IsWindowVisible(hwnd):
            return False
    except OSError:
        return False
    return True


def get_foreground_window() -> int:
    """Return the current foreground HWND without truncating the handle."""
    user32 = _get_user32()
    if user32 is None:
        return 0
    try:
        user32.GetForegroundWindow.restype = wintypes.HWND
        return int(user32.GetForegroundWindow() or 0)
    except OSError:
        return 0


def _force_foreground(hwnd: int) -> bool:
    user32 = _get_user32()
    kernel32 = _get_kernel32()
    if user32 is None or kernel32 is None or not hwnd:
        return False
    try:
        user32.IsIconic.argtypes = [wintypes.HWND]
        user32.IsIconic.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.BringWindowToTop.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        user32.AttachThreadInput.restype = wintypes.BOOL
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, _SW_RESTORE)

        foreground_hwnd = get_foreground_window()
        target_thread = int(user32.GetWindowThreadProcessId(hwnd, None))
        foreground_thread = (
            int(user32.GetWindowThreadProcessId(foreground_hwnd, None))
            if foreground_hwnd
            else 0
        )
        current_thread = int(kernel32.GetCurrentThreadId())
        attached_foreground = False
        attached_target = False
        if foreground_thread and foreground_thread != current_thread:
            attached_foreground = bool(
                user32.AttachThreadInput(current_thread, foreground_thread, True)
            )
        if target_thread and target_thread != current_thread:
            attached_target = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        try:
            user32.BringWindowToTop(hwnd)
            return bool(user32.SetForegroundWindow(hwnd))
        finally:
            if attached_foreground:
                user32.AttachThreadInput(current_thread, foreground_thread, False)
            if attached_target:
                user32.AttachThreadInput(current_thread, target_thread, False)
    except OSError as exc:
        LOGGER.debug("SetForegroundWindow failed for hwnd=%s: %s", hwnd, exc)
        return False


def _focus_child_window(hwnd: int) -> None:
    user32 = _get_user32()
    kernel32 = _get_kernel32()
    if user32 is None or kernel32 is None or not hwnd:
        return
    try:
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
        user32.AttachThreadInput.restype = wintypes.BOOL
        user32.SetFocus.argtypes = [wintypes.HWND]
        user32.SetFocus.restype = wintypes.HWND
        kernel32.GetCurrentThreadId.restype = wintypes.DWORD

        target_thread = int(user32.GetWindowThreadProcessId(hwnd, None))
        current_thread = int(kernel32.GetCurrentThreadId())
        attached = False
        if target_thread and target_thread != current_thread:
            attached = bool(user32.AttachThreadInput(current_thread, target_thread, True))
        try:
            user32.SetFocus(hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(current_thread, target_thread, False)
    except OSError as exc:
        LOGGER.debug("SetFocus failed for hwnd=%s: %s", hwnd, exc)


def _grab_native_rect(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    include_layered_windows: bool = True,
) -> QImage:
    """Capture a native-pixel desktop rectangle using GDI."""
    if width <= 0 or height <= 0:
        return QImage()

    user32 = _get_user32()
    gdi32 = _get_gdi32()
    if user32 is None or gdi32 is None:
        return QImage()

    screen_dc = mem_dc = bitmap = old_obj = None
    try:
        user32.GetDC.restype = wintypes.HDC
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
        gdi32.BitBlt.argtypes = [
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.DWORD,
        ]
        gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(_BITMAPINFO),
            wintypes.UINT,
        ]
        gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
        gdi32.DeleteObject.restype = wintypes.BOOL
        gdi32.DeleteDC.argtypes = [wintypes.HDC]
        gdi32.DeleteDC.restype = wintypes.BOOL

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            return QImage()
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not mem_dc or not bitmap:
            return QImage()

        old_obj = gdi32.SelectObject(mem_dc, bitmap)
        raster_op = _SRCCOPY
        if include_layered_windows:
            raster_op |= _CAPTUREBLT

        if not gdi32.BitBlt(
            mem_dc,
            0,
            0,
            width,
            height,
            screen_dc,
            x,
            y,
            raster_op,
        ):
            return QImage()

        bitmap_info = _BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = _BI_RGB

        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(
            mem_dc,
            bitmap,
            0,
            height,
            buffer,
            ctypes.byref(bitmap_info),
            _DIB_RGB_COLORS,
        )
        if rows != height:
            return QImage()

        return QImage(
            buffer,
            width,
            height,
            width * 4,
            QImage.Format.Format_RGB32,
        ).copy()
    except (AttributeError, OSError) as exc:
        LOGGER.debug("GDI screen capture failed: %s", exc)
        return QImage()
    finally:
        try:
            if old_obj and mem_dc:
                gdi32.SelectObject(mem_dc, old_obj)
            if bitmap:
                gdi32.DeleteObject(bitmap)
            if mem_dc:
                gdi32.DeleteDC(mem_dc)
            if screen_dc:
                user32.ReleaseDC(None, screen_dc)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.debug("GDI cleanup failed: %s", exc)


def grab_screen_region(screen: QScreen, local_rect: QRect) -> QPixmap:
    """Capture a screen-local Qt logical region."""
    x, y, width, height = qt_screen_local_rect_to_native(screen, local_rect)
    image = _grab_native_rect(
        x,
        y,
        width,
        height,
        include_layered_windows=False,
    )
    if not image.isNull():
        return QPixmap.fromImage(image)

    geom = screen.geometry()
    rect = QRect(local_rect).normalized()
    pixmap = screen.grabWindow(
        0,
        geom.x() + rect.x(),
        geom.y() + rect.y(),
        rect.width(),
        rect.height(),
    )
    if not pixmap.isNull() and not _pixmap_is_probably_blank(pixmap):
        return pixmap

    return pixmap


def grab_screen_snapshot(screen: QScreen) -> QPixmap:
    """Capture one screen before any screenshot overlay is shown."""
    full_screen = QRect(QPoint(0, 0), screen.geometry().size())
    x, y, width, height = qt_screen_local_rect_to_native(screen, full_screen)
    image = _grab_native_rect(
        x,
        y,
        width,
        height,
        include_layered_windows=False,
    )
    if not image.isNull():
        return QPixmap.fromImage(image)

    pixmap = screen.grabWindow(0)
    if not pixmap.isNull() and not _pixmap_is_probably_blank(pixmap):
        return pixmap
    return pixmap


def crop_screen_snapshot(snapshot: QPixmap, screen: QScreen, local_rect: QRect) -> QPixmap:
    """Crop a screen-local logical rectangle from a previously captured screen snapshot."""
    if snapshot.isNull():
        return QPixmap()

    screen_size = screen.geometry().size()
    if screen_size.width() <= 0 or screen_size.height() <= 0:
        return QPixmap()

    rect = QRect(local_rect).normalized().intersected(QRect(QPoint(0, 0), screen_size))
    if rect.isEmpty():
        return QPixmap()

    scale_x = snapshot.width() / screen_size.width()
    scale_y = snapshot.height() / screen_size.height()
    source_rect = QRect(
        round(rect.x() * scale_x),
        round(rect.y() * scale_y),
        max(1, round(rect.width() * scale_x)),
        max(1, round(rect.height() * scale_y)),
    ).intersected(snapshot.rect())
    if source_rect.isEmpty():
        return QPixmap()
    return snapshot.copy(source_rect)


def _pixmap_is_probably_blank(pixmap: QPixmap) -> bool:
    """Detect compositor/overlay failures without rejecting ordinary dark regions too eagerly."""
    image = pixmap.toImage()
    if image.isNull():
        return True

    width = image.width()
    height = image.height()
    if width <= 0 or height <= 0:
        return True

    samples = 0
    blank = 0
    steps = 8
    for row in range(steps):
        y = min(height - 1, round((row + 1) * height / (steps + 1)))
        for col in range(steps):
            x = min(width - 1, round((col + 1) * width / (steps + 1)))
            color = image.pixelColor(x, y)
            samples += 1
            if color.alpha() <= 2 or (
                color.red() <= 2 and color.green() <= 2 and color.blue() <= 2
            ):
                blank += 1
    return samples > 0 and (blank / samples) > 0.98


def grab_window_pixmap(hwnd: int) -> QPixmap:
    """Capture a top-level window's visible bounds."""
    rect = get_window_rect_dwm(hwnd)
    if rect is not None:
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        image = _grab_native_rect(int(rect.left), int(rect.top), width, height)
        if not image.isNull():
            return QPixmap.fromImage(image)

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QPixmap()
    return screen.grabWindow(hwnd)


def _send_input_mouse(user32, inputs: list[_INPUT]) -> bool:
    user32.SendInput.argtypes = [
        wintypes.UINT,
        ctypes.POINTER(_INPUT),
        ctypes.c_int,
    ]
    user32.SendInput.restype = wintypes.UINT
    input_array = (_INPUT * len(inputs))(*inputs)
    sent = user32.SendInput(len(inputs), input_array, ctypes.sizeof(_INPUT))
    return int(sent) == len(inputs)


def _mouse_input(flags: int, mouse_data: int = 0) -> _INPUT:
    return _INPUT(
        type=_INPUT_MOUSE,
        union=_InputUnion(
            mi=_MOUSEINPUT(
                dx=0,
                dy=0,
                mouseData=wintypes.DWORD(mouse_data & 0xFFFFFFFF),
                dwFlags=flags,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )


def _pack_signed_words(low: int, high: int) -> int:
    return (low & 0xFFFF) | ((high & 0xFFFF) << 16)


def _post_wheel_scroll(
    hwnd: int,
    x: int,
    y: int,
    *,
    notches: int = 2,
    upward: bool = False,
) -> bool:
    """Post wheel messages directly to the selected child/control HWND."""
    user32 = _get_user32()
    if user32 is None or not hwnd:
        return False
    try:
        user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        user32.PostMessageW.restype = wintypes.BOOL
        delta = _WHEEL_DELTA if upward else -_WHEEL_DELTA
        wparam = wintypes.WPARAM((_pack_signed_words(0, delta)))
        lparam = wintypes.LPARAM(_pack_signed_words(x, y))
        ok = False
        for _ in range(max(1, notches)):
            ok = bool(user32.PostMessageW(hwnd, _WM_MOUSEWHEEL, wparam, lparam)) or ok
        return ok
    except OSError as exc:
        LOGGER.debug("Failed to post mouse wheel scroll: %s", exc)
        return False


def _send_wheel_scroll_at(x: int, y: int, *, notches: int = 2, upward: bool = False) -> bool:
    """Scroll at a native screen point using mouse-wheel input."""
    user32 = _get_user32()
    if user32 is None:
        return False
    delta = _WHEEL_DELTA if upward else -_WHEEL_DELTA
    try:
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        user32.SetCursorPos(x, y)
        inputs = [
            _mouse_input(_MOUSEEVENTF_WHEEL, delta)
            for _ in range(max(1, notches))
        ]
        if _send_input_mouse(user32, inputs):
            return True
    except OSError as exc:
        LOGGER.warning("Failed to send mouse wheel scroll: %s", exc)
    try:
        user32.mouse_event.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_int,
            _ULONG_PTR,
        ]
        user32.SetCursorPos(x, y)
        for _ in range(max(1, notches)):
            user32.mouse_event(_MOUSEEVENTF_WHEEL, 0, 0, delta, 0)
        return True
    except OSError as exc:
        LOGGER.warning("Fallback mouse wheel scroll failed: %s", exc)
        return False


def _click_native_point(x: int, y: int) -> bool:
    """Move the cursor to a native screen point and left-click once.

    Chromium/Electron windows (VS Code, browsers, most modern editors) ignore a
    synthetic mouse wheel until the pane has been activated by a real click, so
    this is what makes wheel scrolling actually take effect on those targets.
    """
    user32 = _get_user32()
    if user32 is None:
        return False
    try:
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        user32.SetCursorPos(x, y)
        if _send_input_mouse(
            user32,
            [
                _mouse_input(_MOUSEEVENTF_LEFTDOWN),
                _mouse_input(_MOUSEEVENTF_LEFTUP),
            ],
        ):
            return True
    except OSError as exc:
        LOGGER.warning("Failed to focus scroll target by click: %s", exc)
    try:
        user32.mouse_event.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.c_int,
            _ULONG_PTR,
        ]
        user32.SetCursorPos(x, y)
        user32.mouse_event(_MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        user32.mouse_event(_MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        return True
    except OSError as exc:
        LOGGER.warning("Fallback click failed: %s", exc)
        return False


def are_images_similar(img1: QImage, img2: QImage, threshold: float = 0.997) -> bool:
    """Compare two images on a small grid."""
    if img1.size() != img2.size():
        return False

    width = img1.width()
    height = img1.height()
    if width <= 0 or height <= 0:
        return True

    x_step = max(1, width // 96)
    y_step = max(1, height // 96)
    diff = 0
    max_diff = 0
    for y in range(0, height, y_step):
        for x in range(0, width, x_step):
            left = img1.pixelColor(x, y)
            right = img2.pixelColor(x, y)
            diff += (
                abs(left.red() - right.red())
                + abs(left.green() - right.green())
                + abs(left.blue() - right.blue())
            )
            max_diff += 3 * 255

    similarity = 1.0 - (diff / max(1, max_diff))
    return similarity >= threshold


def _row_signatures(image: QImage, columns: list[int]) -> list[tuple[int, ...]]:
    signatures: list[tuple[int, ...]] = []
    for y in range(image.height()):
        row: list[int] = []
        for x in columns:
            value = image.pixel(x, y)
            row.extend(((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF))
        signatures.append(tuple(row))
    return signatures


def _offset_fallback(height: int, expected_offset: int | None) -> int:
    """Best-guess scroll offset when content matching is not conclusive."""
    if expected_offset is not None:
        return min(max(1, expected_offset), max(1, height - 1))
    return max(1, int(height * 0.8))


def _stride_notches(
    frame_height: int,
    px_per_notch: float | None,
    margin_px: int,
    fallback_notches: int,
) -> int:
    """Wheel notches needed to advance nearly one viewport per scroll step.

    Until pixels-per-notch has been calibrated from a measured frame shift,
    a small fixed step is used so the first stitch can act as calibration.
    """
    if px_per_notch is None or px_per_notch <= 0 or frame_height <= 0:
        return fallback_notches
    margin = max(margin_px, int(frame_height * 0.08))
    target = frame_height - margin
    if target <= 0:
        return fallback_notches
    return max(1, min(200, int(target / px_per_notch)))


def find_vertical_offset(
    img1: QImage,
    img2: QImage,
    expected_offset: int | None = None,
) -> int | None:
    """Find the vertical pixel offset of img2 relative to img1."""
    height = img1.height()
    width = img1.width()
    if img1.size() != img2.size():
        return None
    if height <= 50 or width <= 50:
        return _offset_fallback(height, expected_offset)

    column_count = min(33, max(9, width // 45))
    columns = [
        min(width - 1, round((index + 1) * width / (column_count + 1)))
        for index in range(column_count)
    ]
    sigs1 = _row_signatures(img1, columns)
    sigs2 = _row_signatures(img2, columns)

    # Search from dy=1: the final scroll before the bottom can be clamped to
    # a few pixels, so small offsets must stay in range.
    min_dy = 1
    max_dy = min(height - 1, int(height * 0.96))
    if max_dy <= min_dy:
        return _offset_fallback(height, expected_offset)

    sample_step = max(2, height // 180)

    def score(dy: int) -> float:
        overlap = height - dy
        if overlap <= 0:
            return float("inf")
        diff = 0
        comparisons = 0
        for row in range(0, overlap, sample_step):
            a = sigs1[dy + row]
            b = sigs2[row]
            diff += sum(abs(left - right) for left, right in zip(a, b))
            comparisons += len(a)
        return diff / max(1, comparisons)

    # Score every candidate shift. The true alignment can be a 1px-sharp
    # minimum that a coarse grid straddles and misses, so it is scanned at
    # full resolution.
    scores = [score(dy) for dy in range(min_dy, max_dy + 1)]
    best_score = min(scores)

    # Prefer the SMALLEST shift that aligns essentially as well as the best.
    # The average-diff metric structurally favors large dy — a tiny overlap has
    # few rows left to disagree on, so spurious near-bottom matches score low
    # and over-estimate the offset, duplicating rows at the first and last
    # seams. The real shift is the smallest near-perfect alignment.
    tolerance = max(2.0, best_score * 0.4)
    refined = min_dy
    chosen_score = best_score
    for index, value in enumerate(scores):
        if value <= best_score + tolerance:
            refined = min_dy + index
            chosen_score = value
            break

    if chosen_score < 18.0:
        return refined

    fallback = _offset_fallback(height, expected_offset)
    LOGGER.warning(
        "Stitching: best match had high difference: %.2f (dy=%s). Using fallback %d.",
        chosen_score,
        refined,
        fallback,
    )
    return fallback


def stitch_images(images: list[QImage], offsets: list[int]) -> QImage | None:
    """Stitch a sequence of images together using calculated offsets."""
    if not images:
        return None
    if len(images) == 1:
        return images[0]

    width = images[0].width()
    height = images[0].height()
    total_height = height + sum(offsets)

    stitched = QImage(width, total_height, QImage.Format.Format_ARGB32)
    stitched.fill(0)

    painter = QPainter(stitched)
    painter.drawImage(0, 0, images[0])

    y = height
    for index, offset in enumerate(offsets):
        append_height = max(1, min(offset, images[index + 1].height()))
        source_y = max(0, images[index + 1].height() - append_height)
        painter.drawImage(
            QRect(0, y, width, append_height),
            images[index + 1],
            QRect(0, source_y, width, append_height),
        )
        y += append_height

    painter.end()
    return stitched


class ScrollingScreenshotCoordinator(QObject):
    """Orchestrates scrolling screenshots step-by-step using a QTimer.

    The capture runs in two phases: the scroll target is first driven back to
    its top with upward wheel bursts (probing for a scroll method that
    actually moves the content), then frames are captured while scrolling
    down until the content stops changing, and stitched into one image.
    """

    finished = pyqtSignal(QImage)
    failed = pyqtSignal(str)

    _PHASE_IDLE = "idle"
    _PHASE_TO_TOP = "to_top"
    _PHASE_CAPTURE = "capture"

    def __init__(
        self,
        parent_widget,
        max_pages: int = 60,
        scroll_delay_ms: int = 380,
        initial_delay_ms: int = 180,
        allow_self_capture: bool = False,
        scroll_notches: int = 2,
        step_margin_px: int = 100,
        to_top_max_bursts: int = 60,
        to_top_delay_ms: int = 170,
        to_top_notches: int = 15,
        debug_dir: str | None = None,
        prefer_input_injection: bool = False,
    ) -> None:
        super().__init__(parent_widget)
        self.parent_widget = parent_widget
        self.max_pages = max_pages
        self.scroll_delay_ms = scroll_delay_ms
        self.initial_delay_ms = initial_delay_ms
        self.allow_self_capture = allow_self_capture
        self.debug_dir = debug_dir
        # Skip the (async, unreliable) PostMessage method and scroll purely with
        # SendInput cursor-wheel injection — the path that works on Chromium/
        # Electron targets like VS Code and browsers.
        self.prefer_input_injection = prefer_input_injection
        self.scroll_notches = scroll_notches
        self.step_margin_px = step_margin_px
        self.to_top_max_bursts = to_top_max_bursts
        self.to_top_delay_ms = to_top_delay_ms
        self.to_top_notches = to_top_notches

        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self._on_timer_tick)

        self.hwnd = 0
        self.scroll_hwnd = 0
        self.viewport_screen: QScreen | None = None
        self.viewport_rect = QRect()
        self.scroll_point_native: tuple[int, int] | None = None
        self.scroll_point_candidates: list[tuple[int, int]] = []
        self.scroll_plan: list[tuple[str, tuple[int, int]]] = []
        self.scroll_method_index = 0
        self.scroll_method_locked = False
        self.images: list[QImage] = []
        self.offsets: list[int] = []
        self.last_size = QSize()
        self._phase = self._PHASE_IDLE
        self._top_last_frame: QImage | None = None
        self._top_bursts = 0
        self._cursor_origin: tuple[int, int] | None = None
        self._cursor_moved = False
        self._px_per_notch: float | None = None
        self._last_sent_notches = scroll_notches

    def start(
        self,
        target_hwnd: int = 0,
        scroll_hwnd: int = 0,
        viewport_screen: QScreen | None = None,
        viewport_rect: QRect | None = None,
    ) -> bool:
        """Initialize and start the scrolling screenshot capture sequence."""
        user32 = _get_user32()
        if user32 is None:
            self.failed.emit("Windows screen capture APIs are unavailable.")
            return False

        self.hwnd = target_hwnd or get_foreground_window()
        self.scroll_hwnd = scroll_hwnd or self.hwnd
        self.viewport_screen = viewport_screen
        self.viewport_rect = QRect(viewport_rect) if viewport_rect is not None else QRect()
        self.scroll_point_native = None
        self.scroll_point_candidates = []
        self.scroll_method_index = 0
        self.scroll_method_locked = False

        if not is_valid_capture_window(self.hwnd, self._own_capture_hwnd()):
            self.failed.emit("Selected window is invalid for scrolling screenshot.")
            return False

        if self.viewport_screen is None or self.viewport_rect.isEmpty():
            viewport = (
                hwnd_client_screen_local_rect(self.scroll_hwnd)
                or hwnd_client_screen_local_rect(self.hwnd)
            )
            if viewport is not None:
                self.viewport_screen, self.viewport_rect = viewport

        if self.viewport_screen is None or self.viewport_rect.isEmpty():
            self.failed.emit("Could not determine scrollable capture area.")
            return False

        self.scroll_point_native = screen_local_rect_center_to_native(
            self.viewport_screen,
            self.viewport_rect,
        )
        qt_global_center = screen_local_rect_center_to_global(
            self.viewport_screen,
            self.viewport_rect,
        )
        self.scroll_point_candidates = [self.scroll_point_native]
        if qt_global_center != self.scroll_point_native:
            self.scroll_point_candidates.append(qt_global_center)
        self.scroll_plan = self._build_scroll_plan()

        self._cursor_origin = _current_cursor_native_point()
        self._cursor_moved = False
        # A real click is what activates Chromium/Electron panes so a synthetic
        # wheel is honored; without it those targets silently ignore scrolling.
        self._activate_scroll_target(click=True)
        QApplication.processEvents()

        self.images = []
        self.offsets = []
        self.last_size = QSize()
        self._top_last_frame = None
        self._top_bursts = 0
        self._phase = self._PHASE_TO_TOP
        self.timer.start(self.initial_delay_ms)
        return True

    def _own_capture_hwnd(self) -> int:
        try:
            own_hwnd = int(self.parent_widget.winId())
        except (AttributeError, ValueError):
            own_hwnd = 0
        return 0 if self.allow_self_capture else own_hwnd

    def _grab_frame(self) -> QImage | None:
        if self.viewport_screen is not None and not self.viewport_rect.isEmpty():
            pixmap = grab_screen_region(self.viewport_screen, self.viewport_rect)
        else:
            pixmap = grab_window_pixmap(self.hwnd)
        if pixmap.isNull():
            return None
        image = pixmap.toImage()
        return None if image.isNull() else image

    def _on_timer_tick(self) -> None:
        if self._phase == self._PHASE_TO_TOP:
            self._scroll_to_top_step()
        elif self._phase == self._PHASE_CAPTURE:
            self._capture_step()

    def _scroll_to_top_step(self) -> None:
        """Drive the scroll target back to its top with upward wheel bursts.

        Doubles as a probe for a working scroll method: once content visibly
        moves, the current method is locked in for the capture phase.
        """
        if not is_valid_capture_window(self.hwnd, self._own_capture_hwnd()):
            self._fail("Target window is no longer available.")
            return

        frame = self._grab_frame()
        if frame is None:
            self._fail("Screen capture failed.")
            return

        moved = (
            self._top_last_frame is None
            or self._top_last_frame.size() != frame.size()
            or not are_images_similar(self._top_last_frame, frame)
        )
        click_to_activate = False
        if moved:
            if self._top_last_frame is not None:
                self.scroll_method_locked = True
            self._top_last_frame = frame
            self._top_bursts += 1
            if self._top_bursts > self.to_top_max_bursts:
                LOGGER.info("Scroll-to-top burst limit reached. Starting capture.")
                self._begin_capture()
                return
        elif not self.scroll_method_locked and self._advance_scroll_method():
            # The current method moved nothing; re-click to activate the pane
            # before trying the next one.
            LOGGER.info("Scroll-to-top is probing a fallback scroll method.")
            click_to_activate = True
        else:
            self._begin_capture()
            return

        self._activate_scroll_target(click=click_to_activate)
        if not self._send_scroll(upward=True, notches=self.to_top_notches):
            self._begin_capture()
            return
        self.timer.start(self.to_top_delay_ms)

    def _begin_capture(self) -> None:
        if not self.scroll_method_locked:
            self.scroll_method_index = 0
        self._top_last_frame = None
        self.images = []
        self.offsets = []
        self.last_size = QSize()
        self._px_per_notch = None
        self._last_sent_notches = self.scroll_notches
        self._phase = self._PHASE_CAPTURE
        self.timer.start(self.initial_delay_ms)

    def _capture_step(self) -> None:
        if not is_valid_capture_window(self.hwnd, self._own_capture_hwnd()):
            LOGGER.info("Target window became invalid during scrolling screenshot.")
            self._finish()
            return

        new_img = self._grab_frame()
        if new_img is None:
            self._fail("Screen capture failed.")
            return

        if not self.images:
            self.images.append(new_img)
            self.last_size = new_img.size()
            self._scroll_and_continue()
            return

        if new_img.size() != self.last_size:
            LOGGER.info("Window resized during scrolling screenshot. Finishing.")
            self._finish()
            return

        if are_images_similar(self.images[-1], new_img):
            if len(self.images) == 1 and self._try_next_scroll_method():
                return
            LOGGER.info("Content similarity suggests bottom of window is reached.")
            self._finish()
            return

        expected_offset = None
        if self._px_per_notch is not None and self._last_sent_notches > 0:
            expected_offset = max(1, round(self._px_per_notch * self._last_sent_notches))
        elif self.offsets:
            sorted_offsets = sorted(self.offsets)
            expected_offset = sorted_offsets[len(sorted_offsets) // 2]
        offset = find_vertical_offset(self.images[-1], new_img, expected_offset)
        if offset is None:
            LOGGER.warning("Stitching alignment failed. Finishing.")
            self._finish()
            return

        self.images.append(new_img)
        self.offsets.append(offset)
        per_notch = offset / max(1, self._last_sent_notches)
        if self._px_per_notch is None:
            self._px_per_notch = per_notch
        else:
            self._px_per_notch = (self._px_per_notch + per_notch) / 2.0

        if len(self.images) >= self.max_pages:
            LOGGER.info("Reached maximum page limit.")
            self._finish()
            return

        self._scroll_and_continue()

    def _scroll_and_continue(self) -> None:
        self._activate_scroll_target()
        notches = _stride_notches(
            self.last_size.height() if not self.last_size.isEmpty() else 0,
            self._px_per_notch,
            self.step_margin_px,
            self.scroll_notches,
        )
        self._last_sent_notches = notches
        if not self._send_scroll(notches=notches):
            self._finish()
            return
        self.timer.start(self.scroll_delay_ms)

    def _build_scroll_plan(self) -> list[tuple[str, tuple[int, int]]]:
        """Ordered list of (method, native-point) scroll attempts.

        PostMessage to the control is tried first (cheap, doesn't move the
        cursor) unless input injection is forced, then SendInput wheel at each
        coordinate candidate. Probing walks this list until one moves content.
        """
        plan: list[tuple[str, tuple[int, int]]] = []
        if (
            not self.prefer_input_injection
            and self.scroll_hwnd
            and self.scroll_point_native is not None
        ):
            plan.append(("post", self.scroll_point_native))
        for point in self.scroll_point_candidates:
            plan.append(("input", point))
        return plan

    def _current_plan_entry(self) -> tuple[str, tuple[int, int]] | None:
        if not self.scroll_plan:
            return None
        index = min(self.scroll_method_index, len(self.scroll_plan) - 1)
        return self.scroll_plan[index]

    def _advance_scroll_method(self) -> bool:
        if self.scroll_method_index >= len(self.scroll_plan) - 1:
            return False
        self.scroll_method_index += 1
        return True

    def _try_next_scroll_method(self) -> bool:
        if self.scroll_method_locked or not self._advance_scroll_method():
            return False
        LOGGER.info("Retrying scrolling screenshot with fallback scroll method.")
        self._activate_scroll_target(click=True)
        self._scroll_and_continue()
        return True

    def _send_scroll(self, *, upward: bool = False, notches: int | None = None) -> bool:
        count = self.scroll_notches if notches is None else notches
        entry = self._current_plan_entry()
        if entry is None:
            return False
        kind, (x, y) = entry
        if kind == "post":
            return _post_wheel_scroll(self.scroll_hwnd, x, y, notches=count, upward=upward)
        self._cursor_moved = True
        return _send_wheel_scroll_at(x, y, notches=count, upward=upward)

    def _scroll_point_for_method(self) -> tuple[int, int] | None:
        """Native point the current scroll method targets.

        The activation click must land on the same point the wheel will use —
        otherwise the click can miss the pane on DPI-scaled/multi-monitor setups
        and the target is never activated for that method.
        """
        entry = self._current_plan_entry()
        if entry is not None:
            return entry[1]
        return self.scroll_point_native

    def _activate_scroll_target(self, *, click: bool = False) -> None:
        _force_foreground(self.hwnd)
        if self.scroll_hwnd:
            _focus_child_window(self.scroll_hwnd)
        point = self._scroll_point_for_method()
        if click and point is not None:
            _click_native_point(*point)
            self._cursor_moved = True
            if self.scroll_hwnd:
                _focus_child_window(self.scroll_hwnd)

    def _restore_cursor(self) -> None:
        if not self._cursor_moved or self._cursor_origin is None:
            return
        user32 = _get_user32()
        if user32 is None:
            return
        try:
            user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
            user32.SetCursorPos.restype = wintypes.BOOL
            user32.SetCursorPos(*self._cursor_origin)
        except OSError as exc:
            LOGGER.debug("Failed to restore cursor position: %s", exc)
        self._cursor_moved = False

    def _finish(self) -> None:
        self.timer.stop()
        self._phase = self._PHASE_IDLE
        self._restore_cursor()
        if not self.images:
            self.failed.emit("No frames captured.")
            return

        stitched = stitch_images(self.images, self.offsets)
        self._dump_debug(stitched)
        if stitched is None:
            self.failed.emit("Stitching failed.")
            return
        if len(self.images) <= 1:
            LOGGER.warning(
                "Scrolling capture produced a single frame — the target never "
                "scrolled. Returning a static screenshot of the visible area."
            )
        self.finished.emit(stitched)

    def _dump_debug(self, stitched: QImage | None) -> None:
        """Save raw frames, the stitched result, and metrics when debugging.

        Enabled by setting the TASKBAR_SCROLL_DEBUG_DIR environment variable;
        the dump shows whether frames actually moved between scroll steps.
        """
        debug_dir = os.environ.get("TASKBAR_SCROLL_DEBUG_DIR") or self.debug_dir
        if not debug_dir:
            return
        try:
            os.makedirs(debug_dir, exist_ok=True)
            for index, image in enumerate(self.images):
                image.save(os.path.join(debug_dir, f"frame_{index:03d}.png"))
            if stitched is not None:
                stitched.save(os.path.join(debug_dir, "stitched.png"))
            meta = {
                "frame_count": len(self.images),
                "offsets": self.offsets,
                "px_per_notch": self._px_per_notch,
                "scroll_method_index": self.scroll_method_index,
                "scroll_method_locked": self.scroll_method_locked,
                "scroll_hwnd": self.scroll_hwnd,
                "capture_hwnd": self.hwnd,
                "viewport_rect": [
                    self.viewport_rect.x(),
                    self.viewport_rect.y(),
                    self.viewport_rect.width(),
                    self.viewport_rect.height(),
                ],
            }
            with open(os.path.join(debug_dir, "debug.json"), "w", encoding="utf-8") as handle:
                json.dump(meta, handle, indent=2)
            LOGGER.info("Wrote scroll capture debug dump to %s", debug_dir)
        except OSError as exc:
            LOGGER.warning("Failed to write scroll debug dump: %s", exc)

    def _fail(self, reason: str) -> None:
        self.timer.stop()
        self._phase = self._PHASE_IDLE
        self._restore_cursor()
        self.failed.emit(reason)

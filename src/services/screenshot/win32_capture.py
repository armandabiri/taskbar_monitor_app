"""GDI / PrintWindow screen and window capture."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QGuiApplication, QImage, QPainter, QPixmap, QScreen

from services.screenshot.win32_common import (
    _BI_RGB,
    _BITMAPINFO,
    _BITMAPINFOHEADER,
    _DIB_RGB_COLORS,
    _PW_RENDERFULLCONTENT,
    LOGGER,
    _get_gdi32,
    _get_user32,
)
from services.screenshot.win32_gdi import grab_native_rect
from services.screenshot.window_geometry import (
    get_window_rect_dwm,
    qt_screen_local_rect_to_native,
)


def grab_screen_region(screen: QScreen, local_rect: QRect) -> QPixmap:
    """Capture a screen-local Qt logical region."""
    x, y, width, height = qt_screen_local_rect_to_native(screen, local_rect)
    image = grab_native_rect(
        x,
        y,
        width,
        height,
        include_layered_windows=False,
    )
    if not image.isNull():
        pixmap = QPixmap.fromImage(image)
        # A non-null but all-black grab means the compositor handed back an empty
        # surface (common right after windows reorder); fall through to Qt's
        # grabWindow rather than returning a blank strip into the stitch.
        if not _pixmap_is_probably_blank(pixmap):
            return pixmap

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
    image = grab_native_rect(
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


def grab_virtual_desktop() -> QImage:
    """Capture every monitor stitched into one image by its virtual layout.

    Each screen snapshot is drawn into a canvas sized to the union of all
    screen geometries (logical pixels), so a multi-monitor desktop comes back
    as a single wide image with each screen in its real relative position.
    """
    screens = QGuiApplication.screens()
    if not screens:
        return QImage()
    virtual = QRect()
    for screen in screens:
        virtual = virtual.united(screen.geometry())
    if virtual.isEmpty():
        return QImage()

    canvas = QImage(virtual.width(), virtual.height(), QImage.Format.Format_RGB32)
    canvas.fill(0)
    painter = QPainter(canvas)
    try:
        for screen in screens:
            pixmap = grab_screen_snapshot(screen)
            if pixmap.isNull():
                continue
            geom = screen.geometry()
            target = QRect(
                geom.x() - virtual.x(),
                geom.y() - virtual.y(),
                geom.width(),
                geom.height(),
            )
            painter.drawPixmap(target, pixmap)
    finally:
        painter.end()
    return canvas


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


def _print_window_image(hwnd: int) -> QImage:
    """Render a window into a bitmap via PrintWindow, even when occluded.

    Unlike a screen BitBlt, PrintWindow asks the window to paint itself, so it
    works when the target is partially covered and never captures windows
    stacked on top of it. PW_RENDERFULLCONTENT makes it work for DWM/Chromium/
    Direct-composition windows that would otherwise come back black.
    """
    user32 = _get_user32()
    gdi32 = _get_gdi32()
    if user32 is None or gdi32 is None or not hwnd:
        return QImage()

    rect = wintypes.RECT()
    try:
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return QImage()
    except OSError:
        return QImage()

    width = int(rect.right - rect.left)
    height = int(rect.bottom - rect.top)
    if width <= 0 or height <= 0:
        return QImage()

    screen_dc = mem_dc = bitmap = old_obj = None
    try:
        user32.GetDC.restype = wintypes.HDC
        user32.GetDC.argtypes = [wintypes.HWND]
        user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
        user32.PrintWindow.restype = wintypes.BOOL
        gdi32.CreateCompatibleDC.restype = wintypes.HDC
        gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
        gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
        gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.SelectObject.restype = wintypes.HGDIOBJ
        gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]

        screen_dc = user32.GetDC(None)
        if not screen_dc:
            return QImage()
        mem_dc = gdi32.CreateCompatibleDC(screen_dc)
        bitmap = gdi32.CreateCompatibleBitmap(screen_dc, width, height)
        if not mem_dc or not bitmap:
            return QImage()
        old_obj = gdi32.SelectObject(mem_dc, bitmap)

        if not user32.PrintWindow(hwnd, mem_dc, _PW_RENDERFULLCONTENT):
            return QImage()

        bitmap_info = _BITMAPINFO()
        bitmap_info.bmiHeader.biSize = ctypes.sizeof(_BITMAPINFOHEADER)
        bitmap_info.bmiHeader.biWidth = width
        bitmap_info.bmiHeader.biHeight = -height
        bitmap_info.bmiHeader.biPlanes = 1
        bitmap_info.bmiHeader.biBitCount = 32
        bitmap_info.bmiHeader.biCompression = _BI_RGB

        gdi32.GetDIBits.argtypes = [
            wintypes.HDC,
            wintypes.HBITMAP,
            wintypes.UINT,
            wintypes.UINT,
            ctypes.c_void_p,
            ctypes.POINTER(_BITMAPINFO),
            wintypes.UINT,
        ]
        buffer = ctypes.create_string_buffer(width * height * 4)
        rows = gdi32.GetDIBits(
            mem_dc, bitmap, 0, height, buffer, ctypes.byref(bitmap_info), _DIB_RGB_COLORS
        )
        if rows != height:
            return QImage()
        return QImage(buffer, width, height, width * 4, QImage.Format.Format_RGB32).copy()
    except (AttributeError, OSError) as exc:
        LOGGER.debug("PrintWindow capture failed: %s", exc)
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
            LOGGER.debug("PrintWindow cleanup failed: %s", exc)


def grab_window_pixmap(hwnd: int) -> QPixmap:
    """Capture a top-level window's visible bounds.

    PrintWindow is tried first so the capture is clean even when the window is
    partially covered; a plain screen BitBlt and Qt's grabWindow are fallbacks
    for the windows PrintWindow renders black.
    """
    printed = _print_window_image(hwnd)
    if not printed.isNull():
        pixmap = QPixmap.fromImage(printed)
        if not _pixmap_is_probably_blank(pixmap):
            return pixmap

    rect = get_window_rect_dwm(hwnd)
    if rect is not None:
        width = int(rect.right - rect.left)
        height = int(rect.bottom - rect.top)
        image = grab_native_rect(int(rect.left), int(rect.top), width, height)
        if not image.isNull():
            pixmap = QPixmap.fromImage(image)
            if not _pixmap_is_probably_blank(pixmap):
                return pixmap

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return QPixmap()
    return screen.grabWindow(hwnd)


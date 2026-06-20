"""Low-level GDI desktop rectangle capture."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from PyQt6.QtGui import QImage

from services.screenshot.win32_common import (
    _BI_RGB,
    _BITMAPINFO,
    _BITMAPINFOHEADER,
    _CAPTUREBLT,
    _DIB_RGB_COLORS,
    _SRCCOPY,
    LOGGER,
    _get_gdi32,
    _get_user32,
)


def grab_native_rect(
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

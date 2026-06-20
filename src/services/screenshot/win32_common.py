"""Win32 ctypes helpers shared by screenshot capture and scroll input."""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes

LOGGER = logging.getLogger(__name__)

_CAPTUREBLT = 0x40000000
_SRCCOPY = 0x00CC0020
_PW_RENDERFULLCONTENT = 0x00000002
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

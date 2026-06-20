"""Synthetic mouse wheel and click input for scroll capture."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from services.screenshot.win32_common import (
    _INPUT,
    _INPUT_MOUSE,
    _MOUSEEVENTF_LEFTDOWN,
    _MOUSEEVENTF_LEFTUP,
    _MOUSEEVENTF_WHEEL,
    _MOUSEINPUT,
    _ULONG_PTR,
    _WHEEL_DELTA,
    _WM_MOUSEWHEEL,
    LOGGER,
    _get_user32,
    _InputUnion,
)


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


def _restore_cursor_position(x: int, y: int) -> bool:
    """Move the cursor back to a saved native screen point."""
    user32 = _get_user32()
    if user32 is None:
        return False
    try:
        user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
        user32.SetCursorPos.restype = wintypes.BOOL
        user32.SetCursorPos(x, y)
        return True
    except OSError as exc:
        LOGGER.debug("Failed to restore cursor position: %s", exc)
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


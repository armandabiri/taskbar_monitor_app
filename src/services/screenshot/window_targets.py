"""Window hit-testing, selection, and focus helpers for capture."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PyQt6.QtCore import QPoint

from services.screenshot.win32_common import (
    _GA_ROOT,
    _POINT,
    _SW_RESTORE,
    LOGGER,
    _get_kernel32,
    _get_user32,
)
from services.screenshot.window_geometry import (
    qt_global_point_to_native,
)


@dataclass(frozen=True)
class WindowSelection:
    """Window handles resolved from a scroll-selector click."""

    capture_hwnd: int
    scroll_hwnd: int

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

"""Native monitor enumeration and screen matching (physical pixels)."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

from PyQt6.QtGui import QGuiApplication, QScreen

from services.screenshot.win32_common import LOGGER, _get_user32


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

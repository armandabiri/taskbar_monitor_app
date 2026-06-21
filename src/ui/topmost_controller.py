"""TopmostController — pins a Qt window above the Win11 taskbar.

Owns the Win32 extended-style application, the WM_WINDOWPOSCHANGING
interception that rewrites ``hwndInsertAfter`` in real time, and the 2 s
safety-net timer that re-asserts topmost when no Z-order message fires.

Extracted from ``src/main.py`` (T01). Public API:

* ``attach(widget)`` — record the hwnd and apply styles.
* ``apply()`` / ``enforce()`` — re-assert topmost.
* ``set_click_through(enabled)`` — toggle WS_EX_LAYERED/TRANSPARENT.
* ``handle_native_event(event_type, message)`` — feed from ``nativeEvent``.
* ``start_safety_timer(parent, interval_ms=2000)`` / ``stop_safety_timer()``.
"""

from __future__ import annotations

import ctypes
import logging

from PyQt6.QtCore import QObject, QTimer

LOGGER = logging.getLogger(__name__)


_GWL_EXSTYLE = -20
_WS_EX_TOOLWINDOW = 0x00000080
_WS_EX_TOPMOST = 0x00000008
_WS_EX_NOACTIVATE = 0x08000000
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_HWND_TOPMOST = -1
_HWND_NOTOPMOST = -2
_SWP_NOMOVE = 0x0002
_SWP_NOSIZE = 0x0001
_SWP_NOACTIVATE = 0x0010
_SWP_NOZORDER = 0x0004
_SWP_FRAMECHANGED = 0x0020
_WM_WINDOWPOSCHANGING = 0x0046


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_ssize_t),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_ssize_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_ulong),
        ("pt", _POINT),
        ("lPrivate", ctypes.c_ulong),
    ]


class _WINDOWPOS(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_ssize_t),
        ("hwndInsertAfter", ctypes.c_ssize_t),
        ("x", ctypes.c_int),
        ("y", ctypes.c_int),
        ("cx", ctypes.c_int),
        ("cy", ctypes.c_int),
        ("flags", ctypes.c_uint),
    ]


class TopmostController(QObject):
    """Owns topmost enforcement for a single Qt widget."""

    def __init__(self) -> None:
        super().__init__()
        self._hwnd: int = 0
        self._applied = False
        self._click_through = False
        self._widget = None
        self._timer: QTimer | None = None

    def attach(self, widget) -> None:
        self._widget = widget
        self._hwnd = int(widget.winId())
        self.apply()

    @property
    def hwnd(self) -> int:
        return self._hwnd

    @property
    def applied(self) -> bool:
        return self._applied

    def set_click_through_preference(self, enabled: bool) -> None:
        self._click_through = enabled

    def apply(self) -> None:
        """Idempotently set Win32 extended styles and assert topmost."""
        if self._widget is None:
            return
        try:
            self._hwnd = int(self._widget.winId())
            user32 = ctypes.windll.user32
            cur = user32.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
            new_style = cur | _WS_EX_TOOLWINDOW | _WS_EX_TOPMOST | _WS_EX_NOACTIVATE
            if self._click_through:
                new_style |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT
            if new_style != cur:
                user32.SetWindowLongW(self._hwnd, _GWL_EXSTYLE, new_style)
            user32.SetWindowPos(
                self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
            )
            self._applied = True
        except OSError as exc:
            LOGGER.warning("Failed to apply Win32 topmost styles: %s", exc)

    def enforce(self) -> None:
        """Re-assert topmost via the toggle trick; safe to call often."""
        if not self._hwnd or self._widget is None or not self._widget.isVisible():
            return
        try:
            user32 = ctypes.windll.user32
            flags = _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE
            user32.SetWindowPos(self._hwnd, _HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            user32.SetWindowPos(self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0, flags)
        except OSError:
            pass

    def set_click_through(self, enabled: bool) -> None:
        """Apply WS_EX_LAYERED|TRANSPARENT to the current hwnd."""
        self._click_through = enabled
        if not self._hwnd:
            return
        try:
            user32 = ctypes.windll.user32
            cur = user32.GetWindowLongW(self._hwnd, _GWL_EXSTYLE)
            if enabled:
                cur |= _WS_EX_LAYERED | _WS_EX_TRANSPARENT
            else:
                cur &= ~(_WS_EX_TRANSPARENT | _WS_EX_LAYERED)
            user32.SetWindowLongW(self._hwnd, _GWL_EXSTYLE, cur)
            user32.SetWindowPos(
                self._hwnd, _HWND_TOPMOST, 0, 0, 0, 0,
                _SWP_NOMOVE | _SWP_NOSIZE | _SWP_NOACTIVATE | _SWP_FRAMECHANGED,
            )
        except OSError as exc:
            LOGGER.warning("Failed to toggle click-through: %s", exc)

    def handle_native_event(self, event_type, message) -> None:
        """Rewrite WM_WINDOWPOSCHANGING in-place to keep us HWND_TOPMOST."""
        try:
            if event_type == b"windows_generic_MSG" and self._applied:
                msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
                if msg.message == _WM_WINDOWPOSCHANGING and msg.lParam:
                    wp = ctypes.cast(msg.lParam, ctypes.POINTER(_WINDOWPOS)).contents
                    if not (wp.flags & _SWP_NOZORDER):
                        wp.hwndInsertAfter = _HWND_TOPMOST
        except (ValueError, OSError):
            pass

    def start_safety_timer(self, parent, interval_ms: int = 2000) -> QTimer:
        timer = QTimer(parent)
        timer.setInterval(interval_ms)
        timer.timeout.connect(self.enforce)
        timer.start()
        self._timer = timer
        return timer

    def stop_safety_timer(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

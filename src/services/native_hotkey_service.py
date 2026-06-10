"""Windows native global hotkey registration."""

from __future__ import annotations

import ctypes
import itertools
import logging
import sys
import threading
from ctypes import wintypes
from typing import Callable

from PyQt6.QtCore import QAbstractNativeEventFilter, QCoreApplication

LOGGER = logging.getLogger(__name__)

_WM_HOTKEY = 0x0312
_MOD_ALT = 0x0001
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_WIN = 0x0008
_MOD_NOREPEAT = 0x4000


class _POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class _MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", _POINT),
    ]


_MODIFIER_ALIASES = {
    "alt": _MOD_ALT,
    "option": _MOD_ALT,
    "ctrl": _MOD_CONTROL,
    "control": _MOD_CONTROL,
    "shift": _MOD_SHIFT,
    "win": _MOD_WIN,
    "windows": _MOD_WIN,
    "cmd": _MOD_WIN,
    "command": _MOD_WIN,
}

_KEY_ALIASES = {
    "backspace": 0x08,
    "tab": 0x09,
    "enter": 0x0D,
    "return": 0x0D,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "page up": 0x21,
    "pageup": 0x21,
    "page down": 0x22,
    "pagedown": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "insert": 0x2D,
    "ins": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "print screen": 0x2C,
    "printscreen": 0x2C,
    "pause": 0x13,
    "=": 0xBB,
    "+": 0xBB,
    "plus": 0xBB,
    "-": 0xBD,
    "minus": 0xBD,
    "`": 0xC0,
    "~": 0xC0,
    "[": 0xDB,
    "]": 0xDD,
    "\\": 0xDC,
    ";": 0xBA,
    "'": 0xDE,
    ",": 0xBC,
    ".": 0xBE,
    "/": 0xBF,
}

_NEXT_HOTKEY_ID = itertools.count(0xB000)
_ID_LOCK = threading.Lock()


def parse_hotkey(hotkey: str) -> tuple[int, int] | None:
    """Parse a keyboard-library style hotkey into RegisterHotKey modifiers/VK."""
    modifiers = 0
    key_vk: int | None = None
    for raw_part in hotkey.lower().split("+"):
        part = raw_part.strip()
        if not part:
            continue
        modifier = _MODIFIER_ALIASES.get(part)
        if modifier is not None:
            modifiers |= modifier
            continue
        if key_vk is not None:
            return None
        if len(part) == 1 and ("a" <= part <= "z" or "0" <= part <= "9"):
            key_vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            number = int(part[1:])
            if not 1 <= number <= 24:
                return None
            key_vk = 0x70 + number - 1
        else:
            key_vk = _KEY_ALIASES.get(part)

    if key_vk is None:
        return None
    return (modifiers | _MOD_NOREPEAT, key_vk)


class _NativeHotkeyFilter(QAbstractNativeEventFilter):
    def __init__(self, registrar: "NativeHotkeyRegistrar") -> None:
        super().__init__()
        self._registrar = registrar

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type not in (b"windows_generic_MSG", b"windows_dispatcher_MSG"):
            return False, 0
        try:
            msg = ctypes.cast(int(message), ctypes.POINTER(_MSG)).contents
        except (TypeError, ValueError):
            return False, 0
        if int(msg.message) != _WM_HOTKEY:
            return False, 0
        if self._registrar.dispatch(int(msg.wParam)):
            return True, 0
        return False, 0


class NativeHotkeyRegistrar:
    """Registers process-wide Windows hotkeys and dispatches WM_HOTKEY."""

    def __init__(self, *, enabled: bool | None = None) -> None:
        self.enabled = sys.platform == "win32" if enabled is None else enabled
        self._callbacks: dict[int, Callable[[], object]] = {}
        self._hotkey_ids_by_chord: dict[str, int] = {}
        self._filter: _NativeHotkeyFilter | None = None
        self._user32 = None

    def register(self, hotkey: str, callback: Callable[[], object]) -> bool | None:
        """Register one hotkey.

        Returns True on native registration, False on native API failure, and
        None when native registration is unsupported for this chord/platform.
        """
        if not self.enabled:
            return None
        parsed = parse_hotkey(hotkey)
        if parsed is None:
            return None
        app = QCoreApplication.instance()
        if app is None:
            return None

        self._ensure_filter(app)
        user32 = self._get_user32()
        if user32 is None:
            return None

        modifiers, vk = parsed
        with _ID_LOCK:
            hotkey_id = next(_NEXT_HOTKEY_ID)
        try:
            user32.RegisterHotKey.argtypes = [
                wintypes.HWND,
                ctypes.c_int,
                wintypes.UINT,
                wintypes.UINT,
            ]
            user32.RegisterHotKey.restype = wintypes.BOOL
            if not user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                error = ctypes.get_last_error()
                LOGGER.warning("RegisterHotKey failed for %r: winerror=%s", hotkey, error)
                return False
        except OSError as exc:
            LOGGER.warning("RegisterHotKey failed for %r: %s", hotkey, exc)
            return False

        self._callbacks[hotkey_id] = callback
        self._hotkey_ids_by_chord[hotkey] = hotkey_id
        return True

    def unregister_all(self) -> None:
        user32 = self._get_user32()
        if user32 is not None:
            try:
                user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
                user32.UnregisterHotKey.restype = wintypes.BOOL
                for hotkey_id in list(self._callbacks):
                    user32.UnregisterHotKey(None, hotkey_id)
            except OSError as exc:
                LOGGER.debug("UnregisterHotKey failed: %s", exc)
        self._callbacks.clear()
        self._hotkey_ids_by_chord.clear()
        app = QCoreApplication.instance()
        if app is not None and self._filter is not None:
            app.removeNativeEventFilter(self._filter)
        self._filter = None

    def dispatch(self, hotkey_id: int) -> bool:
        callback = self._callbacks.get(hotkey_id)
        if callback is None:
            return False
        try:
            callback()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Native hotkey callback failed: %s", exc)
        return True

    def _ensure_filter(self, app: QCoreApplication) -> None:
        if self._filter is None:
            self._filter = _NativeHotkeyFilter(self)
            app.installNativeEventFilter(self._filter)

    def _get_user32(self):
        if self._user32 is not None:
            return self._user32
        if sys.platform != "win32":
            return None
        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        except (AttributeError, OSError):
            self._user32 = None
        return self._user32

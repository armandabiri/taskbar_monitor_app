"""Synthetic keyboard input (Ctrl+V paste) for the capture clipboard stack."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from services.screenshot.win32_common import (
    _INPUT,
    _INPUT_KEYBOARD,
    _KEYBDINPUT,
    _KEYEVENTF_KEYUP,
    _VK_CONTROL,
    _VK_V,
    LOGGER,
    _get_user32,
    _InputUnion,
)


def _key_input(vk: int, *, up: bool) -> _INPUT:
    return _INPUT(
        type=_INPUT_KEYBOARD,
        union=_InputUnion(
            ki=_KEYBDINPUT(
                wVk=vk,
                wScan=0,
                dwFlags=_KEYEVENTF_KEYUP if up else 0,
                time=0,
                dwExtraInfo=0,
            )
        ),
    )


def send_ctrl_v() -> bool:
    """Send a synthetic Ctrl+V keystroke to the focused window."""
    user32 = _get_user32()
    if user32 is None:
        return False
    inputs = [
        _key_input(_VK_CONTROL, up=False),
        _key_input(_VK_V, up=False),
        _key_input(_VK_V, up=True),
        _key_input(_VK_CONTROL, up=True),
    ]
    try:
        user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(_INPUT),
            ctypes.c_int,
        ]
        user32.SendInput.restype = wintypes.UINT
        array = (_INPUT * len(inputs))(*inputs)
        sent = user32.SendInput(len(inputs), array, ctypes.sizeof(_INPUT))
        return int(sent) == len(inputs)
    except OSError as exc:
        LOGGER.warning("Failed to send Ctrl+V: %s", exc)
        return False

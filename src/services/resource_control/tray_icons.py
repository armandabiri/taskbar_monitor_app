"""Read the PIDs that own Windows notification-area (tray) icons.

Split out of ``windows_ops`` because it is a self-contained, fiddly piece of
cross-process memory reading used only as a 'spare list' input: any process
that owns a tray icon must never be killed.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging

from services.resource_control.constants import PROCESS_QUERY_INFORMATION

LOGGER = logging.getLogger(__name__)

_kernel32 = ctypes.windll.kernel32
_user32 = ctypes.windll.user32

# Toolbar messages used to read tray icons out of Explorer's notification toolbar.
_TB_BUTTONCOUNT = 0x0418
_TB_GETBUTTON = 0x0417

_PROCESS_VM_OPERATION = 0x0008
_PROCESS_VM_READ = 0x0010
_PROCESS_VM_WRITE = 0x0020

_MEM_COMMIT = 0x1000
_MEM_RELEASE = 0x8000
_PAGE_READWRITE = 0x04


def enumerate_tray_icon_pids() -> set[int]:
    """PIDs of processes that own an icon in the Windows notification area.

    Reads Explorer's tray toolbar via cross-process memory; if anything on this
    path fails, returns an empty set so callers fall back to visible-window
    detection only.
    """
    pids: set[int] = set()
    for toolbar in _find_tray_toolbars():
        try:
            pids.update(_read_toolbar_owner_pids(toolbar))
        except OSError as exc:
            LOGGER.debug("Tray toolbar read failed: %s", exc)
    return pids


def _find_tray_toolbars() -> list[int]:
    """Return HWNDs of every notification-area toolbar to inspect."""
    toolbars: list[int] = []

    # Primary tray (always visible)
    tray_wnd = _user32.FindWindowW("Shell_TrayWnd", None)
    if tray_wnd:
        notify_wnd = _user32.FindWindowExW(tray_wnd, 0, "TrayNotifyWnd", None)
        if notify_wnd:
            pager = _user32.FindWindowExW(notify_wnd, 0, "SysPager", None) or notify_wnd
            toolbar = _user32.FindWindowExW(pager, 0, "ToolbarWindow32", None)
            if toolbar:
                toolbars.append(toolbar)

    # Overflow tray (the up-arrow flyout that holds hidden icons)
    overflow = _user32.FindWindowW("NotifyIconOverflowWindow", None)
    if overflow:
        toolbar = _user32.FindWindowExW(overflow, 0, "ToolbarWindow32", None)
        if toolbar:
            toolbars.append(toolbar)

    return toolbars


def _read_toolbar_owner_pids(toolbar_hwnd: int) -> set[int]:
    """Read each TBBUTTON's TRAYDATA via remote memory; return owning PIDs."""
    pids: set[int] = set()

    proc_id = ctypes.wintypes.DWORD()
    _user32.GetWindowThreadProcessId(toolbar_hwnd, ctypes.byref(proc_id))
    if not proc_id.value:
        return pids

    count = _user32.SendMessageW(toolbar_hwnd, _TB_BUTTONCOUNT, 0, 0)
    if count <= 0:
        return pids

    handle = _kernel32.OpenProcess(
        _PROCESS_VM_OPERATION | _PROCESS_VM_READ | _PROCESS_VM_WRITE | PROCESS_QUERY_INFORMATION,
        False,
        proc_id.value,
    )
    if not handle:
        return pids

    try:
        # TBBUTTON is 32 bytes on x64 (8+4+1+1+6+8+8). Conservative buffer.
        tbbutton_size = 32
        buffer_size = 1024
        remote_buf = _kernel32.VirtualAllocEx(
            handle, None, buffer_size, _MEM_COMMIT, _PAGE_READWRITE,
        )
        if not remote_buf:
            return pids

        try:
            tbb_buf = (ctypes.c_byte * tbbutton_size)()
            traydata_buf = (ctypes.c_byte * 8)()
            bytes_read = ctypes.c_size_t()
            for i in range(count):
                if not _user32.SendMessageW(toolbar_hwnd, _TB_GETBUTTON, i, remote_buf):
                    continue
                if not _kernel32.ReadProcessMemory(
                    handle, remote_buf, tbb_buf, tbbutton_size, ctypes.byref(bytes_read),
                ):
                    continue
                # On x64, dwData (DWORD_PTR) lives at offset 16 in TBBUTTON.
                dw_data = ctypes.c_uint64.from_buffer(tbb_buf, 16).value
                if not dw_data:
                    continue
                # TRAYDATA layout starts with HWND hWnd (8 bytes on x64).
                if not _kernel32.ReadProcessMemory(
                    handle, ctypes.c_void_p(dw_data), traydata_buf, 8, ctypes.byref(bytes_read),
                ):
                    continue
                td_hwnd = ctypes.c_uint64.from_buffer(traydata_buf, 0).value
                if not td_hwnd:
                    continue
                icon_pid = ctypes.wintypes.DWORD()
                _user32.GetWindowThreadProcessId(
                    ctypes.wintypes.HWND(td_hwnd), ctypes.byref(icon_pid),
                )
                if icon_pid.value:
                    pids.add(int(icon_pid.value))
        finally:
            _kernel32.VirtualFreeEx(handle, remote_buf, 0, _MEM_RELEASE)
    finally:
        _kernel32.CloseHandle(handle)

    return pids

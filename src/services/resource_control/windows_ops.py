"""Windows-specific process operations used by resource control."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os

import psutil

from services.resource_control.constants import (
    PROCESS_QUERY_INFORMATION,
    PROCESS_QUERY_LIMITED_INFORMATION,
    PROCESS_SET_QUOTA,
    QUOTA_LIMITS_HARDWS_MAX_DISABLE,
    QUOTA_LIMITS_HARDWS_MIN_DISABLE,
    SYSTEM_MEMORY_LIST_INFORMATION,
)
from services.resource_control.models import ThrottleAction

LOGGER = logging.getLogger(__name__)

_kernel32 = ctypes.windll.kernel32
_ntdll = ctypes.windll.ntdll
_user32 = ctypes.windll.user32
_advapi32 = ctypes.windll.advapi32

# Memory list commands for NtSetSystemInformation(SystemMemoryListInformation)
_MEMORY_EMPTY_WORKING_SETS = 2
_MEMORY_FLUSH_MODIFIED_LIST = 3
_MEMORY_PURGE_STANDBY_LIST = 4

# Toolbar messages used to read tray icons out of Explorer's notification toolbar.
_TB_BUTTONCOUNT = 0x0418
_TB_GETBUTTON = 0x0417

# OpenProcess access masks for cross-process VM ops + termination.
_PROCESS_VM_OPERATION = 0x0008
_PROCESS_VM_READ = 0x0010
_PROCESS_VM_WRITE = 0x0020

_MEM_COMMIT = 0x1000
_MEM_RELEASE = 0x8000
_PAGE_READWRITE = 0x04

# Token / privilege constants
_TOKEN_ADJUST_PRIVILEGES = 0x0020
_TOKEN_QUERY = 0x0008
_SE_PRIVILEGE_ENABLED = 0x00000002

_PRIORITY_ORDER = [
    getattr(psutil, "IDLE_PRIORITY_CLASS", 64),
    getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 16384),
    getattr(psutil, "NORMAL_PRIORITY_CLASS", 32),
    getattr(psutil, "ABOVE_NORMAL_PRIORITY_CLASS", 32768),
    getattr(psutil, "HIGH_PRIORITY_CLASS", 128),
    getattr(psutil, "REALTIME_PRIORITY_CLASS", 256),
]
_PRIORITY_INDEX = {value: index for index, value in enumerate(_PRIORITY_ORDER)}

# WinUser callback signature for EnumWindows
_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)


class _LUID(ctypes.Structure):
    _fields_ = [("LowPart", ctypes.c_ulong), ("HighPart", ctypes.c_long)]


class _LUID_AND_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("Luid", _LUID), ("Attributes", ctypes.c_ulong)]


class _TOKEN_PRIVILEGES(ctypes.Structure):
    _fields_ = [
        ("PrivilegeCount", ctypes.c_ulong),
        ("Privileges", _LUID_AND_ATTRIBUTES * 1),
    ]


# Configure return types for things the type-checkers care about.
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.GetCurrentProcess.restype = ctypes.c_void_p
_kernel32.VirtualAllocEx.restype = ctypes.c_void_p
_kernel32.VirtualAllocEx.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong, ctypes.c_ulong,
]
_kernel32.VirtualFreeEx.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_ulong,
]
_kernel32.ReadProcessMemory.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
]
_user32.SendMessageW.restype = ctypes.c_long
_user32.SendMessageW.argtypes = [
    ctypes.wintypes.HWND, ctypes.c_uint, ctypes.wintypes.WPARAM, ctypes.wintypes.LPARAM,
]


class SystemMemoryListCommand(ctypes.c_int):
    MEMORY_PURGE_STANDBY_LIST = _MEMORY_PURGE_STANDBY_LIST


def _try_enable_privilege(name: str) -> bool:
    """Best-effort enable a token privilege. Returns True only if actually enabled."""
    token = ctypes.c_void_p()
    if not _advapi32.OpenProcessToken(
        _kernel32.GetCurrentProcess(),
        _TOKEN_ADJUST_PRIVILEGES | _TOKEN_QUERY,
        ctypes.byref(token),
    ):
        return False
    try:
        luid = _LUID()
        if not _advapi32.LookupPrivilegeValueW(None, name, ctypes.byref(luid)):
            return False
        tp = _TOKEN_PRIVILEGES()
        tp.PrivilegeCount = 1
        tp.Privileges[0].Luid = luid
        tp.Privileges[0].Attributes = _SE_PRIVILEGE_ENABLED
        if not _advapi32.AdjustTokenPrivileges(
            token, False, ctypes.byref(tp), ctypes.sizeof(tp), None, None,
        ):
            return False
        # AdjustTokenPrivileges returns success even if not all privs were assigned.
        # ERROR_NOT_ALL_ASSIGNED = 1300
        return ctypes.GetLastError() != 1300
    finally:
        _kernel32.CloseHandle(token)


class WindowsProcessOperator:
    """Performs low-level Win32 operations used by the resource service."""

    @staticmethod
    def get_foreground_pid() -> int | None:
        try:
            hwnd = _user32.GetForegroundWindow()
            if not hwnd:
                return None
            pid = ctypes.wintypes.DWORD()
            _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            return int(pid.value) or None
        except (AttributeError, OSError):
            return None

    @staticmethod
    def trim_workingset(pid: int) -> float:
        handle = _kernel32.OpenProcess(
            PROCESS_QUERY_INFORMATION | PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_SET_QUOTA,
            False,
            pid,
        )
        if not handle:
            raise OSError(f"OpenProcess failed for pid {pid} (err={ctypes.GetLastError()})")
        try:
            rss_before = float(psutil.Process(pid).memory_info().rss)
            success = _kernel32.SetProcessWorkingSetSizeEx(
                handle,
                ctypes.c_size_t(-1 & 0xFFFFFFFFFFFFFFFF),
                ctypes.c_size_t(-1 & 0xFFFFFFFFFFFFFFFF),
                QUOTA_LIMITS_HARDWS_MIN_DISABLE | QUOTA_LIMITS_HARDWS_MAX_DISABLE,
            )
            if not success:
                raise OSError(
                    f"SetProcessWorkingSetSizeEx failed for pid {pid} "
                    f"(err={ctypes.GetLastError()})"
                )
            rss_after = float(psutil.Process(pid).memory_info().rss)
            return max(rss_before - rss_after, 0.0) / (1024 * 1024)
        finally:
            _kernel32.CloseHandle(handle)

    @staticmethod
    def flush_standby_cache() -> bool:
        _try_enable_privilege("SeProfileSingleProcessPrivilege")
        cmd = ctypes.c_int(_MEMORY_PURGE_STANDBY_LIST)
        status = _ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(cmd), ctypes.sizeof(cmd),
        )
        return status == 0

    @staticmethod
    def empty_all_working_sets() -> bool:
        """System-wide working-set empty. Requires admin (SeProfileSingleProcessPrivilege)."""
        _try_enable_privilege("SeProfileSingleProcessPrivilege")
        cmd = ctypes.c_int(_MEMORY_EMPTY_WORKING_SETS)
        status = _ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(cmd), ctypes.sizeof(cmd),
        )
        return status == 0

    @staticmethod
    def flush_modified_pages() -> bool:
        """Flush the modified-page list to disk. Doesn't require special privilege."""
        cmd = ctypes.c_int(_MEMORY_FLUSH_MODIFIED_LIST)
        status = _ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(cmd), ctypes.sizeof(cmd),
        )
        return status == 0

    @staticmethod
    def enumerate_visible_window_pids() -> set[int]:
        """PIDs of every top-level window where IsWindowVisible() is true.

        Visible includes minimized-to-taskbar windows. This is the primary
        'spare list' input — anything with a UI the user can interact with
        must never be killed.
        """
        pids: set[int] = set()

        @_WNDENUMPROC
        def _cb(hwnd, _lparam):
            try:
                if not _user32.IsWindowVisible(hwnd):
                    return True
                pid = ctypes.wintypes.DWORD()
                _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    pids.add(int(pid.value))
            except OSError:
                pass
            return True

        try:
            _user32.EnumWindows(_cb, 0)
        except OSError as exc:
            LOGGER.warning("EnumWindows failed: %s", exc)
        return pids

    @staticmethod
    def enumerate_tray_icon_pids() -> set[int]:
        """PIDs of processes that own an icon in the Windows notification area.

        Reads Explorer's tray toolbar via cross-process memory; if anything
        on this path fails, returns an empty set so callers fall back to
        visible-window detection only.
        """
        pids: set[int] = set()
        toolbars = _find_tray_toolbars()
        for toolbar in toolbars:
            try:
                pids.update(_read_toolbar_owner_pids(toolbar))
            except OSError as exc:
                LOGGER.debug("Tray toolbar read failed: %s", exc)
        return pids

    @staticmethod
    def terminate_process(pid: int, *, graceful_timeout: float = 1.5,
                          force_timeout: float = 1.0) -> bool:
        """Terminate a process: graceful first, then forceful. Returns True on success."""
        try:
            proc = psutil.Process(pid)
            name = proc.name()  # capture before death
            proc.terminate()
            try:
                proc.wait(timeout=graceful_timeout)
                LOGGER.debug("Terminated pid=%d (%s) gracefully", pid, name)
                return True
            except psutil.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=force_timeout)
                LOGGER.debug("Killed pid=%d (%s) forcefully", pid, name)
                return True
        except psutil.NoSuchProcess:
            return True  # already gone — counts as success
        except (psutil.AccessDenied, psutil.TimeoutExpired) as exc:
            LOGGER.debug("Terminate pid=%d failed: %s", pid, exc)
            return False

    def apply_throttle(self, proc: psutil.Process, action: ThrottleAction) -> tuple[str, ...]:
        applied: set[str] = set()
        if action.priority_class is not None:
            current = self._get_priority(proc)
            target = action.priority_class
            if current is None or self._priority_rank(target) < self._priority_rank(current):
                proc.nice(target)
                applied.add("cpu")
        if action.io_priority is not None and hasattr(proc, "ionice"):
            proc.ionice(action.io_priority)
            applied.add("disk")
        if action.affinity_limit is not None:
            current_affinity = proc.cpu_affinity()
            if len(current_affinity) > action.affinity_limit:
                proc.cpu_affinity(current_affinity[:action.affinity_limit])
                applied.add("cpu")
        return tuple(sorted(applied))

    def _get_priority(self, proc: psutil.Process) -> int | None:
        try:
            value = proc.nice()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return None
        return value if isinstance(value, int) else None

    @staticmethod
    def _priority_rank(value: int) -> int:
        return _PRIORITY_INDEX.get(value, len(_PRIORITY_ORDER))


# ---------------------------------------------------------------------------
# Tray-icon enumeration helpers
# ---------------------------------------------------------------------------

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


def get_own_pid() -> int:
    return os.getpid()

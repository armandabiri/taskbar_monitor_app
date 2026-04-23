"""Windows-specific process operations used by resource control."""

from __future__ import annotations

import ctypes
import ctypes.wintypes

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

_kernel32 = ctypes.windll.kernel32
_ntdll = ctypes.windll.ntdll
_user32 = ctypes.windll.user32

_PRIORITY_ORDER = [
    getattr(psutil, "IDLE_PRIORITY_CLASS", 64),
    getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 16384),
    getattr(psutil, "NORMAL_PRIORITY_CLASS", 32),
    getattr(psutil, "ABOVE_NORMAL_PRIORITY_CLASS", 32768),
    getattr(psutil, "HIGH_PRIORITY_CLASS", 128),
    getattr(psutil, "REALTIME_PRIORITY_CLASS", 256),
]
_PRIORITY_INDEX = {value: index for index, value in enumerate(_PRIORITY_ORDER)}


class SystemMemoryListCommand(ctypes.c_int):
    MEMORY_PURGE_STANDBY_LIST = 4


class WindowsProcessOperator:
    """Performs low-level Win32 operations and safe process throttling."""

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
        command = SystemMemoryListCommand(SystemMemoryListCommand.MEMORY_PURGE_STANDBY_LIST)
        status = _ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFORMATION,
            ctypes.byref(command),
            ctypes.sizeof(command),
        )
        return status == 0

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

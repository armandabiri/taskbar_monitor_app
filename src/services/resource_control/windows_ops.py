"""Windows-specific process operations used by resource control."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
from dataclasses import dataclass

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
from services.resource_control.tray_icons import enumerate_tray_icon_pids
from services.resource_control.win_privilege import try_enable_privilege

LOGGER = logging.getLogger(__name__)

_kernel32 = ctypes.windll.kernel32
_ntdll = ctypes.windll.ntdll
_user32 = ctypes.windll.user32

# Memory list commands for NtSetSystemInformation(SystemMemoryListInformation)
_MEMORY_EMPTY_WORKING_SETS = 2
_MEMORY_FLUSH_MODIFIED_LIST = 3
_MEMORY_PURGE_STANDBY_LIST = 4

_PRIORITY_ORDER = [
    getattr(psutil, "IDLE_PRIORITY_CLASS", 64),
    getattr(psutil, "BELOW_NORMAL_PRIORITY_CLASS", 16384),
    getattr(psutil, "NORMAL_PRIORITY_CLASS", 32),
    getattr(psutil, "ABOVE_NORMAL_PRIORITY_CLASS", 32768),
    getattr(psutil, "HIGH_PRIORITY_CLASS", 128),
    getattr(psutil, "REALTIME_PRIORITY_CLASS", 256),
]
_PRIORITY_INDEX = {value: index for index, value in enumerate(_PRIORITY_ORDER)}


@dataclass(frozen=True)
class ThrottleState:
    """Snapshot of a process's scheduling state before it was throttled.

    Used to restore (undo) a throttle: the prior priority class, IO priority
    and CPU affinity mask are captured before :meth:`WindowsProcessOperator.apply_throttle`
    lowers them.
    """

    priority: int | None
    io_priority: int | None
    affinity: tuple[int, ...] | None

# WinUser callback signature for EnumWindows
_WNDENUMPROC = ctypes.WINFUNCTYPE(
    ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM
)

# Configure return types for things the type-checkers care about.
_kernel32.OpenProcess.restype = ctypes.c_void_p
_kernel32.GetCurrentProcess.restype = ctypes.c_void_p


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
        try_enable_privilege("SeProfileSingleProcessPrivilege")
        cmd = ctypes.c_int(_MEMORY_PURGE_STANDBY_LIST)
        status = _ntdll.NtSetSystemInformation(
            SYSTEM_MEMORY_LIST_INFORMATION, ctypes.byref(cmd), ctypes.sizeof(cmd),
        )
        return status == 0

    @staticmethod
    def empty_all_working_sets() -> bool:
        """System-wide working-set empty. Requires admin (SeProfileSingleProcessPrivilege)."""
        try_enable_privilege("SeProfileSingleProcessPrivilege")
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
        """PIDs of processes that own an icon in the Windows notification area."""
        return enumerate_tray_icon_pids()

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

    @staticmethod
    def snapshot_throttle_state(proc: psutil.Process) -> "ThrottleState | None":
        """Capture a process's current priority / IO priority / affinity.

        Returned snapshot is what :meth:`restore_throttle` needs to undo a
        throttle. Returns ``None`` if even the priority cannot be read.
        """
        try:
            priority = proc.nice()
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError):
            return None
        io_priority: int | None
        try:
            # On Windows psutil.ionice() returns an int IOPRIO constant.
            raw_io = proc.ionice() if hasattr(proc, "ionice") else None
            io_priority = int(raw_io) if isinstance(raw_io, int) else None
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError, ValueError, TypeError):
            io_priority = None
        try:
            affinity = tuple(proc.cpu_affinity())
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError, AttributeError):
            affinity = None
        return ThrottleState(
            priority=priority if isinstance(priority, int) else None,
            io_priority=io_priority,
            affinity=affinity,
        )

    def restore_throttle(self, pid: int, prior: "ThrottleState") -> bool:
        """Restore a process's priority / IO priority / affinity from a snapshot."""
        try:
            proc = psutil.Process(pid)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            return False
        restored = False
        try:
            if prior.priority is not None:
                proc.nice(prior.priority)
                restored = True
            if prior.io_priority is not None and hasattr(proc, "ionice"):
                proc.ionice(prior.io_priority)
                restored = True
            if prior.affinity:
                proc.cpu_affinity(list(prior.affinity))
                restored = True
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError, ValueError) as exc:
            LOGGER.debug("restore_throttle pid=%d partial/failed: %s", pid, exc)
        return restored


def get_own_pid() -> int:
    return os.getpid()

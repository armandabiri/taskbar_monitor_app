"""Safely release highly consumed Windows resources (CPU and RAM).

This module provides routines to:
- Trim working-set memory from processes consuming excessive RAM.
- Flush the system standby-list / file-cache (requires elevated privileges).
- Lower CPU affinity / priority of runaway processes temporarily.
- Invoke Python garbage collection.
"""

import ctypes
import ctypes.wintypes
import gc
import logging
import os
from dataclasses import dataclass, field
from enum import IntEnum

import psutil

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Windows API constants
# ---------------------------------------------------------------------------
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_SET_QUOTA = 0x0100
PROCESS_SET_INFORMATION = 0x0200

# SetProcessWorkingSetSizeEx flags
QUOTA_LIMITS_HARDWS_MIN_DISABLE = 0x00000002
QUOTA_LIMITS_HARDWS_MAX_DISABLE = 0x00000004


class SystemMemoryListCommand(IntEnum):
    """NtSetSystemInformation memory-list commands."""

    MEMORY_PURGE_STANDBY_LIST = 4


# ---------------------------------------------------------------------------
# Data classes for reporting
# ---------------------------------------------------------------------------
@dataclass
class ReleaseResult:
    """Result from a single resource-release run."""

    ram_freed_mb: float = 0.0
    processes_trimmed: int = 0
    trimmed_process_names: list[str] = field(default_factory=list)
    processes_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    gc_collected: int = 0
    standby_flushed: bool = False

    @property
    def summary(self) -> str:
        """One-line human-readable summary."""
        parts = [
            f"Freed ~{self.ram_freed_mb:.0f} MB",
            f"{self.processes_trimmed} procs trimmed",
        ]
        if self.standby_flushed:
            parts.append("standby cache flushed")
        if self.gc_collected:
            parts.append(f"GC collected {self.gc_collected}")
        if self.errors:
            parts.append(f"{len(self.errors)} errors")
        return " | ".join(parts)

    @property
    def details(self) -> str:
        """Detailed multi-line summary including process names."""
        lines = [self.summary]
        if self.trimmed_process_names:
            # Show a subset of names if there are too many
            names = sorted(set(self.trimmed_process_names))
            if len(names) > 10:
                display_names = names[:10] + ["..."]
            else:
                display_names = names
            lines.append(f"Cleaned: {', '.join(display_names)}")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------
_kernel32 = ctypes.windll.kernel32
_ntdll = ctypes.windll.ntdll


def _trim_process_workingset(pid: int) -> float:
    """Trim the working-set of a single process.

    Returns the approximate MB freed (difference in RSS before/after).
    Raises OSError on failure.
    """
    handle = _kernel32.OpenProcess(
        PROCESS_QUERY_INFORMATION | PROCESS_SET_QUOTA, False, pid
    )
    if not handle:
        raise OSError(f"OpenProcess failed for pid {pid} (err={ctypes.GetLastError()})")
    try:
        # Read RSS before
        try:
            rss_before = float(psutil.Process(pid).memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            rss_before = 0.0

        # -1, -1 tells Windows to empty the working set
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

        # Read RSS after
        try:
            rss_after = float(psutil.Process(pid).memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            rss_after = 0.0

        freed = max(rss_before - rss_after, 0.0) / (1024 * 1024)
        return freed
    finally:
        _kernel32.CloseHandle(handle)


def _flush_standby_cache() -> bool:
    """Attempt to purge the system standby-list (requires admin).

    Returns True on success, False otherwise.
    """
    command = ctypes.c_int(SystemMemoryListCommand.MEMORY_PURGE_STANDBY_LIST)
    status = _ntdll.NtSetSystemInformation(
        0x50,  # SystemMemoryListInformation
        ctypes.byref(command),
        ctypes.sizeof(command),
    )
    if status != 0:
        LOGGER.debug("NtSetSystemInformation flush standby returned 0x%08X", status)
        return False
    return True


# ---------------------------------------------------------------------------
# High-level API
# ---------------------------------------------------------------------------
# Minimum RSS (in MB) before a process is considered for trimming.
TRIM_THRESHOLD_MB = 200
# Protected process names that should never be touched.
PROTECTED_NAMES = frozenset({
    "system", "registry", "smss.exe", "csrss.exe", "wininit.exe",
    "services.exe", "lsass.exe", "svchost.exe", "winlogon.exe",
    "dwm.exe", "fontdrvhost.exe", "explorer.exe", "rtkaudioservice.exe",
    "nvcontainer.exe", "nvidia share.exe", "taskmgr.exe", "shellexperiencehost.exe",
    "searchhost.exe", "startmenuexperiencehost.exe",
})


def _get_foreground_pid() -> int | None:
    """Get the PID of the currently focused window."""
    try:
        hwnd = _kernel32.GetForegroundWindow()
        if not hwnd:
            return None
        pid = ctypes.wintypes.DWORD()
        _kernel32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value
    except (AttributeError, OSError):
        return None


def release_resources(
    trim_threshold_mb: float = TRIM_THRESHOLD_MB,
    flush_cache: bool = True,
    run_gc: bool = True,
    aggressive: bool = False,
) -> ReleaseResult:
    """Release consumed Windows resources safely.

    Parameters
    ----------
    trim_threshold_mb:
        Only trim processes whose RSS exceeds this many MB.
        If aggressive=True, this is ignored in favor of a lower threshold.
    flush_cache:
        If True, attempt to flush the system standby cache.
    run_gc:
        If True, run Python garbage collection.
    aggressive:
        If True, use a lower threshold (50MB) and do not skip the foreground process.

    Returns
    -------
    ReleaseResult with statistics.
    """
    result = ReleaseResult()
    own_pid = os.getpid()
    foreground_pid = _get_foreground_pid()

    # Determine thresholds based on mode
    dynamic_threshold = 50.0 if aggressive else trim_threshold_mb

    # ---- 1. Trim working sets ----
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            pid = info["pid"]
            name = (info["name"] or "").lower()

            # Skip self and critical system processes
            if pid == own_pid or name in PROTECTED_NAMES or pid <= 4:
                continue

            # In AutoSmart mode, skip the foreground window
            if not aggressive and pid == foreground_pid:
                continue

            mem = info.get("memory_info")
            if mem is None:
                continue
            rss_mb = mem.rss / (1024 * 1024)
            if rss_mb < dynamic_threshold:
                continue

            freed = _trim_process_workingset(pid)
            result.ram_freed_mb += freed
            result.processes_trimmed += 1
            if name:
                result.trimmed_process_names.append(name)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            result.processes_skipped += 1
        except OSError as exc:
            result.processes_skipped += 1
            result.errors.append(str(exc))
        except Exception as exc:  # pylint: disable=broad-exception-caught
            result.errors.append(f"Unexpected: {exc}")

    # ---- 2. Flush standby cache (admin only) ----
    if flush_cache:
        try:
            result.standby_flushed = _flush_standby_cache()
        except OSError as exc:
            result.errors.append(f"Standby flush error: {exc}")
        except Exception as exc:  # pylint: disable=broad-exception-caught
            result.errors.append(f"Standby flush unexpected: {exc}")

    # ---- 3. Python GC ----
    if run_gc:
        result.gc_collected = gc.collect()

    LOGGER.info(
        "Resource release (%s) completed: %s",
        "Aggressive" if aggressive else "AutoSmart",
        result.summary
    )
    return result

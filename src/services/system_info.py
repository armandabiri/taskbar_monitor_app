"""System information helpers: GPU, battery, temperatures, top processes, fullscreen.

Temperature reads and the NVML/GPU integration now live in the
``services.sensors`` package. This module keeps the stable public facade
(``get_cpu_temp``, ``get_ram_temp``, ``get_gpu_stats``, ``start_background_pollers``)
plus the battery, top-process, and fullscreen helpers, delegating all temperature
work to the in-process ``SensorHub``.
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from dataclasses import dataclass

import psutil

# Re-export GPU telemetry from the sensors package for backward compatibility.
from services.sensors.hub import get_hub
from services.sensors.nvml_backend import GPUStats, get_gpu_stats

LOGGER = logging.getLogger(__name__)

__all__ = [
    "GPUStats",
    "get_gpu_stats",
    "get_cpu_temp",
    "get_ram_temp",
    "get_gpu_temp",
    "get_ssd_temp",
    "start_background_pollers",
    "stop_background_pollers",
    "BatteryStats",
    "get_battery",
    "ProcessRow",
    "get_top_processes",
    "prime_process_cpu",
    "foreground_is_fullscreen",
]


# ---------------------------------------------------------------------------
# Temperature facade — delegates to the in-process SensorHub
# ---------------------------------------------------------------------------
def start_background_pollers() -> None:
    """Kick off background telemetry (the SensorHub). Idempotent."""
    get_hub().start()


def stop_background_pollers() -> None:
    """Stop background telemetry pollers (used on shutdown)."""
    get_hub().stop()


def get_cpu_temp() -> float | None:
    """Return the current CPU temperature in Celsius, or None."""
    return get_hub().cpu_temp_c()


def get_ram_temp() -> float | None:
    """Return the current RAM temperature in Celsius, or None."""
    return get_hub().ram_temp_c()


def get_gpu_temp() -> float | None:
    """Return the current GPU temperature in Celsius, or None."""
    return get_hub().gpu_temp_c()


def get_ssd_temp() -> float | None:
    """Return the current SSD/NVMe temperature in Celsius, or None."""
    return get_hub().ssd_temp_c()


# ---------------------------------------------------------------------------
# Battery
# ---------------------------------------------------------------------------
@dataclass
class BatteryStats:
    """Battery snapshot."""

    percent: float
    plugged: bool
    secs_left: int  # psutil.POWER_TIME_UNLIMITED/-1 when on AC, -2 when unknown


# Battery state changes slowly — cache and refresh at most every few seconds
# rather than calling psutil.sensors_battery() on every UI tick.
_BATTERY_CACHE_TTL = 5.0
_battery_last_value: BatteryStats | None = None
_battery_last_fetched = 0.0


def get_battery() -> BatteryStats | None:
    """Return battery snapshot, or None when no battery is present."""
    global _battery_last_value, _battery_last_fetched
    now = time.monotonic()
    if now - _battery_last_fetched < _BATTERY_CACHE_TTL:
        return _battery_last_value
    try:
        bat = psutil.sensors_battery()
    except (OSError, AttributeError):
        _battery_last_value = None
        _battery_last_fetched = now
        return None
    if bat is None:
        _battery_last_value = None
    else:
        _battery_last_value = BatteryStats(
            percent=float(bat.percent),
            plugged=bool(bat.power_plugged),
            secs_left=int(bat.secsleft) if bat.secsleft is not None else -2,
        )
    _battery_last_fetched = now
    return _battery_last_value


# ---------------------------------------------------------------------------
# Top processes
# ---------------------------------------------------------------------------
@dataclass
class ProcessRow:
    """Single top-process entry."""

    pid: int
    name: str
    cpu_percent: float
    ram_mb: float


TOP_PROC_MIN_RSS_MB = 10.0  # below this a process is never a "top" candidate


def get_top_processes(limit: int = 5, sort_by: str = "cpu") -> list[ProcessRow]:
    """Return the top-N processes sorted by cpu or ram.

    Optimized to minimize per-iteration syscall cost:
      * Skips tiny processes (< TOP_PROC_MIN_RSS_MB) before any cpu_percent call.
      * Uses heapq.nlargest instead of sorting the full list.

    sort_by: "cpu" or "ram".
    """
    import heapq

    own_pid = os.getpid()
    rows: list[ProcessRow] = []
    for proc in psutil.process_iter(["pid", "name", "memory_info"]):
        try:
            info = proc.info
            pid = info["pid"]
            if pid == own_pid or pid <= 4:
                continue
            mem = info.get("memory_info")
            ram_mb = (mem.rss / (1024 * 1024)) if mem else 0.0
            # Skip tiny processes early — avoids the cpu_percent syscall for
            # the long tail of small/idle processes that can never be top-N.
            if ram_mb < TOP_PROC_MIN_RSS_MB:
                continue
            cpu = proc.cpu_percent(interval=None)
            rows.append(ProcessRow(
                pid=pid,
                name=(info["name"] or "?"),
                cpu_percent=float(cpu),
                ram_mb=float(ram_mb),
            ))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key = (lambda r: r.cpu_percent) if sort_by == "cpu" else (lambda r: r.ram_mb)
    return heapq.nlargest(limit, rows, key=key)


def prime_process_cpu() -> None:
    """Prime per-process CPU counters so the first ``get_top_processes`` call
    returns meaningful values.

    Matches ``get_top_processes``'s pre-filter (skips small/system processes)
    so we don't prime hundreds of tiny processes that can never appear in the
    Top Processes popup. Yields the GIL periodically so the UI thread stays
    responsive while we run on a background thread.
    """
    own_pid = os.getpid()
    primed = 0
    for proc in psutil.process_iter(["pid", "memory_info"]):
        try:
            info = proc.info
            pid = info["pid"]
            if pid == own_pid or pid <= 4:
                continue
            mem = info.get("memory_info")
            ram_mb = (mem.rss / (1024 * 1024)) if mem else 0.0
            if ram_mb < TOP_PROC_MIN_RSS_MB:
                continue
            proc.cpu_percent(interval=None)
            primed += 1
            # Yield the GIL every 20 processes so the UI thread can paint and
            # process input even if many candidates qualify.
            if primed % 20 == 0:
                time.sleep(0.001)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


# ---------------------------------------------------------------------------
# Fullscreen detection (for auto-hide)
# ---------------------------------------------------------------------------
_user32 = ctypes.windll.user32


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


def foreground_is_fullscreen() -> bool:
    """Return True when the foreground window occupies a whole monitor.

    Excludes the desktop/shell windows so the taskbar/wallpaper doesn't
    trip the heuristic.
    """
    try:
        hwnd = _user32.GetForegroundWindow()
        if not hwnd:
            return False

        shell = _user32.GetShellWindow()
        desktop = _user32.GetDesktopWindow()
        if hwnd in (shell, desktop):
            return False

        rect = _RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False

        # Pick the monitor the window lives on
        monitor = _user32.MonitorFromWindow(hwnd, 2)  # MONITOR_DEFAULTTONEAREST
        if not monitor:
            return False

        class MONITORINFO(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_ulong),
                ("rcMonitor", _RECT),
                ("rcWork", _RECT),
                ("dwFlags", ctypes.c_ulong),
            ]

        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not _user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return False

        mr = info.rcMonitor
        return (
            rect.left <= mr.left
            and rect.top <= mr.top
            and rect.right >= mr.right
            and rect.bottom >= mr.bottom
        )
    except OSError:
        return False

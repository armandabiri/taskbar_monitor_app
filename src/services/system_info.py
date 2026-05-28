"""System information helpers: GPU, battery, temperatures, top processes, fullscreen detection."""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging
import os
from dataclasses import dataclass
from typing import Callable

import psutil

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional pynvml for NVIDIA GPU telemetry
# ---------------------------------------------------------------------------
_NVML_READY = False
_NVML: object | None = None
_NVML_HANDLE: object | None = None

try:
    import pynvml as _pynvml_mod  # type: ignore
    _NVML = _pynvml_mod
except ImportError:
    _NVML = None


def _init_nvml() -> None:
    """Initialize NVML lazily; safe to call many times."""
    global _NVML_READY, _NVML_HANDLE
    if _NVML_READY or _NVML is None:
        return
    try:
        _NVML.nvmlInit()  # type: ignore[attr-defined]
        _NVML_HANDLE = _NVML.nvmlDeviceGetHandleByIndex(0)  # type: ignore[attr-defined]
        _NVML_READY = True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.debug("NVML unavailable: %s", exc)
        _NVML_HANDLE = None
        _NVML_READY = False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------
@dataclass
class GPUStats:
    """GPU telemetry snapshot. All fields may be None when unavailable."""

    util_percent: float | None = None
    vram_used_mb: float | None = None
    vram_total_mb: float | None = None
    temp_c: float | None = None

    @property
    def available(self) -> bool:
        """Return True if any field is populated."""
        return any(v is not None for v in (self.util_percent, self.vram_used_mb, self.temp_c))

    @property
    def vram_percent(self) -> float | None:
        """VRAM utilization percentage, or None when unavailable."""
        if self.vram_used_mb is None or not self.vram_total_mb:
            return None
        return (self.vram_used_mb / self.vram_total_mb) * 100.0


@dataclass
class BatteryStats:
    """Battery snapshot."""

    percent: float
    plugged: bool
    secs_left: int  # psutil.POWER_TIME_UNLIMITED/-1 when on AC, -2 when unknown


@dataclass
class ProcessRow:
    """Single top-process entry."""

    pid: int
    name: str
    cpu_percent: float
    ram_mb: float


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------
# NVML calls aren't free — at "Ultra" 100 ms intervals three of them per tick
# is noticeable. Cache results for a short window so fast UI rates don't
# multiply NVML cost.
_GPU_CACHE_TTL = 0.45
_gpu_cache: GPUStats = GPUStats()
_gpu_cache_at = 0.0


def get_gpu_stats() -> GPUStats:
    """Return GPU stats for device 0. Safe if NVML not installed."""
    global _gpu_cache, _gpu_cache_at
    if _NVML is None:
        return GPUStats()
    _init_nvml()
    if not _NVML_READY or _NVML_HANDLE is None:
        return GPUStats()

    now = time.monotonic()
    if now - _gpu_cache_at < _GPU_CACHE_TTL:
        return _gpu_cache

    stats = GPUStats()
    try:
        util = _NVML.nvmlDeviceGetUtilizationRates(_NVML_HANDLE)  # type: ignore[attr-defined]
        stats.util_percent = float(util.gpu)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.debug("GPU util query failed: %s", exc)
    try:
        mem = _NVML.nvmlDeviceGetMemoryInfo(_NVML_HANDLE)  # type: ignore[attr-defined]
        stats.vram_used_mb = mem.used / (1024 * 1024)
        stats.vram_total_mb = mem.total / (1024 * 1024)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.debug("GPU memory query failed: %s", exc)
    try:
        stats.temp_c = float(
            _NVML.nvmlDeviceGetTemperature(_NVML_HANDLE, 0)  # type: ignore[attr-defined]
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.debug("GPU temp query failed: %s", exc)
    _gpu_cache = stats
    _gpu_cache_at = now
    return stats


_PDH_READY = False
_PDH_QUERY = None
_PDH_COUNTER = None

def _init_pdh_temp() -> None:
    global _PDH_READY, _PDH_QUERY, _PDH_COUNTER
    if _PDH_READY:
        return
    try:
        import ctypes
        from ctypes import wintypes
        pdh = ctypes.windll.pdh
        
        query = wintypes.HANDLE()
        pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))
        
        counter = wintypes.HANDLE()
        res = pdh.PdhAddEnglishCounterW(query, r"\Thermal Zone Information(*)\Temperature", 0, ctypes.byref(counter))
        if res == 0:
            _PDH_QUERY = query
            _PDH_COUNTER = counter
            _PDH_READY = True
    except Exception as exc:
        LOGGER.debug("PDH initialization failed: %s", exc)

def get_pdh_cpu_temp() -> float | None:
    _init_pdh_temp()
    if not _PDH_READY:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        pdh = ctypes.windll.pdh
        pdh.PdhCollectQueryData(_PDH_QUERY)
        
        class PDH_FMT_COUNTERVALUE(ctypes.Structure):
            _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]
            
        val = PDH_FMT_COUNTERVALUE()
        res = pdh.PdhGetFormattedCounterValue(_PDH_COUNTER, 0x200, None, ctypes.byref(val))
        if res == 0:
            # 301.0 K (27.85 C) is a static fake ACPI thermal zone value on many Windows systems.
            if val.doubleValue == 301.0:
                return None
            # Thermal Zone Information returns Kelvin
            return val.doubleValue - 273.15
    except Exception as exc:
        LOGGER.debug("PDH collect failed: %s", exc)
    return None


import urllib.request
import json
import threading
import time

# ---------------------------------------------------------------------------
# LibreHardwareMonitor (LHM) telemetry — background poller
#
# LHM exposes a JSON tree at http://127.0.0.1:8085/data.json. Previously the
# UI thread fetched and walked that tree every stats tick, which blocked on
# the TCP timeout whenever LHM wasn't running. Now a daemon thread polls in
# the background and the UI thread only reads cached scalars.
# ---------------------------------------------------------------------------
_LHM_POLL_OK_INTERVAL = 2.0       # poll cadence while LHM is responsive
_LHM_POLL_FAIL_MAX = 60.0         # back off up to this when LHM is down
_LHM_HTTP_TIMEOUT = 0.5

_CPU_NAME_HINTS = ("CPU PACKAGE", "CORE AVERAGE", "CORE MAX", "CPU CORE")
_CPU_PARENT_HINTS = ("INTEL", "CPU")
_RAM_NAME_HINTS = ("DDR", "DIMM", "TEMPERATURE")
_RAM_PARENT_HINTS = ("MEMORY", "CORSAIR", "DIMM", "DDR")


def _collect_lhm_sensors(
    data: dict,
    sensor_type: str,
    name_hints: tuple[str, ...],
    parent_hints: tuple[str, ...],
    current_parent: str = "",
) -> list[float]:
    temps: list[float] = []
    name = data.get("Text", "").upper()
    if data.get("Type", "") == sensor_type:
        if any(h in name for h in name_hints) or any(h in current_parent for h in parent_hints):
            val_str = data.get("Value", "").split(" ")[0].replace(",", ".")
            try:
                temps.append(float(val_str))
            except ValueError:
                pass

    next_parent = name if not current_parent else current_parent + " " + name
    for child in data.get("Children", []):
        temps.extend(
            _collect_lhm_sensors(child, sensor_type, name_hints, parent_hints, next_parent)
        )
    return temps


class _LhmPoller:
    """Background poller for LibreHardwareMonitor temperatures.

    Keeping the HTTP fetch off the UI thread is the whole point of this class
    — when LHM isn't running, the connect attempt can block long enough to
    visibly stutter the monitor.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cpu_temp: float | None = None
        self._ram_temp: float | None = None
        self._available = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._consecutive_failures = 0
        self._started = False

    def ensure_started(self) -> None:
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(target=self._run, name="lhm-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.is_set():
            wait = self._poll_once()
            self._stop.wait(wait)

    def _poll_once(self) -> float:
        try:
            req = urllib.request.Request("http://127.0.0.1:8085/data.json", method="GET")
            with urllib.request.urlopen(req, timeout=_LHM_HTTP_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
        except Exception:  # pylint: disable=broad-exception-caught
            with self._lock:
                self._cpu_temp = None
                self._ram_temp = None
                self._available = False
            self._consecutive_failures += 1
            # 2s, 4s, 8s, 16s, 32s, capped at 60s — avoids hammering a closed port.
            backoff = _LHM_POLL_OK_INTERVAL * (2 ** min(5, self._consecutive_failures))
            return min(_LHM_POLL_FAIL_MAX, backoff)

        self._consecutive_failures = 0
        cpu_temps = _collect_lhm_sensors(data, "Temperature", _CPU_NAME_HINTS, _CPU_PARENT_HINTS)
        ram_temps = _collect_lhm_sensors(data, "Temperature", _RAM_NAME_HINTS, _RAM_PARENT_HINTS)
        with self._lock:
            self._cpu_temp = cpu_temps[0] if cpu_temps else None
            self._ram_temp = (sum(ram_temps) / len(ram_temps)) if ram_temps else None
            self._available = True
        return _LHM_POLL_OK_INTERVAL

    def snapshot(self) -> tuple[float | None, float | None, bool]:
        with self._lock:
            return self._cpu_temp, self._ram_temp, self._available


_LHM = _LhmPoller()


def start_background_pollers() -> None:
    """Kick off any background telemetry pollers. Idempotent."""
    _LHM.ensure_started()


def stop_background_pollers() -> None:
    """Stop background telemetry pollers (used on shutdown)."""
    _LHM.stop()


def get_cpu_temp() -> float | None:
    """Return a CPU temperature reading if any sensor is available."""
    _LHM.ensure_started()
    cpu_temp, _ram_temp, lhm_ok = _LHM.snapshot()
    if lhm_ok and cpu_temp is not None:
        return cpu_temp

    sensors_fn: Callable[[], dict] | None = getattr(psutil, "sensors_temperatures", None)
    if sensors_fn is not None:
        try:
            readings = sensors_fn()
            if readings:
                for _name, entries in readings.items():
                    for entry in entries:
                        current = getattr(entry, "current", None)
                        if current is not None:
                            return float(current)
        except (OSError, AttributeError, NotImplementedError):
            pass

    pdh_temp = get_pdh_cpu_temp()
    if pdh_temp is not None:
        return pdh_temp

    return None


def get_ram_temp() -> float | None:
    """Return average RAM temperature if DDR/DIMM sensors are available."""
    _LHM.ensure_started()
    _cpu_temp, ram_temp, lhm_ok = _LHM.snapshot()
    if lhm_ok and ram_temp is not None:
        return ram_temp

    sensors_fn: Callable[[], dict] | None = getattr(psutil, "sensors_temperatures", None)
    if sensors_fn is not None:
        try:
            readings = sensors_fn()
            if readings:
                temps = []
                for name, entries in readings.items():
                    for entry in entries:
                        lbl = getattr(entry, "label", "").upper()
                        n = name.upper()
                        if "DDR" in n or "DDR" in lbl or "DIMM" in n or "DIMM" in lbl:
                            current = getattr(entry, "current", None)
                            if current is not None:
                                temps.append(float(current))
                if temps:
                    return sum(temps) / len(temps)
        except (OSError, AttributeError, NotImplementedError):
            pass
    return None


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

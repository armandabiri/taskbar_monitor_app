"""NVIDIA NVML GPU telemetry and sensor backend.

Owns the optional ``pynvml`` integration: utilization, VRAM, and GPU temperature
for device 0. ``GPUStats`` and ``get_gpu_stats`` are re-exported from
``services.system_info`` for backward compatibility with existing callers.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from services.sensors.backend import BackendStatus
from services.sensors.models import SensorReading

LOGGER = logging.getLogger(__name__)

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


# NVML calls aren't free — at "Ultra" 100 ms intervals three per tick is
# noticeable. Cache results for a short window so fast UI rates don't multiply
# NVML cost.
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


class NvmlBackend:
    """GPU temperature (and util/VRAM) via NVML."""

    id = "nvml"

    def __init__(self) -> None:
        self._last_ok = False
        self._detail = "not polled yet"

    def available(self) -> bool:
        return _NVML is not None

    def status(self) -> BackendStatus:
        detail = self._detail if _NVML is not None else "pynvml not installed"
        return BackendStatus(self.id, self._last_ok, detail)

    def close(self) -> None:
        return None

    def read(self) -> SensorReading:
        now = time.monotonic()
        stats = get_gpu_stats()
        self._last_ok = stats.temp_c is not None
        self._detail = "ok" if self._last_ok else "no GPU temperature"
        return SensorReading(
            taken_at=now,
            gpu_temp_c=stats.temp_c,
            gpu_util_percent=stats.util_percent,
            vram_percent=stats.vram_percent,
            backend_id=self.id,
        )

"""SensorHub — the single, thread-safe source of truth for temperatures.

The hub owns a background thread that refreshes the active backend chain every
``REFRESH_INTERVAL`` seconds and caches a merged ``SensorReading``. The UI thread
only ever reads the cached snapshot, so a slow or blocking backend never stutters
the monitor. Backends are read in priority order and merged: the highest-priority
backend that reports a given temperature wins, and lower-priority backends fill
any gaps.
"""

from __future__ import annotations

import logging
import threading
import time

from services.sensors.backend import BackendStatus, SensorBackend
from services.sensors.models import SensorReading
from services.sensors.resolver import resolve

LOGGER = logging.getLogger(__name__)

REFRESH_INTERVAL = 2.0


class SensorHub:
    """Aggregate temperatures from an ordered backend chain off the UI thread."""

    def __init__(self, source: str = "auto") -> None:
        self._source = source
        self._backends: list[SensorBackend] = []
        self._reading = SensorReading()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._started = False

    # -- lifecycle ------------------------------------------------------
    def start(self) -> None:
        """Resolve backends, take one reading, log the active backend, then poll."""
        if self._started:
            return
        self._started = True
        self._backends = resolve(self._source)
        self.refresh_once()
        self._log_active()
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="sensor-hub", daemon=True)
        self._thread.start()

    def stop(self, timeout_s: float = 2.0) -> None:
        """Signal the refresh thread, join it, then close backends and finalize NVML.

        Idempotent: a second call is a no-op so closeEvent/aboutToQuit may both fire.
        Stops reads BEFORE closing native handles to avoid a backend.read()/close race.
        """
        if not self._started:
            return
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.0, timeout_s))
        for backend in self._backends:
            try:
                backend.close()
            except Exception:  # pylint: disable=broad-exception-caught
                LOGGER.exception("sensors: backend %s close failed", backend.id)
        # Finalize NVML once everything that could call into it has stopped.
        from services.sensors.nvml_backend import nvml_shutdown
        try:
            nvml_shutdown()
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("sensors: nvml_shutdown failed")
        self._started = False

    def reload(self, source: str) -> None:
        """Rebuild the backend chain for a new source and refresh immediately."""
        self._source = source if source in ("auto", "clr", "http") else "auto"
        for backend in self._backends:
            backend.close()
        self._backends = resolve(self._source)
        self.refresh_once()
        self._log_active()

    # -- polling --------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            self._stop.wait(REFRESH_INTERVAL)
            if self._stop.is_set():
                break
            self.refresh_once()

    def refresh_once(self) -> SensorReading:
        """Read every backend in order, merge, cache, and return the snapshot."""
        merged = SensorReading(taken_at=time.monotonic())
        for backend in self._backends:
            try:
                merged = merged.merged_with(backend.read())
            except Exception as exc:  # pylint: disable=broad-exception-caught
                LOGGER.debug("sensors: backend %s read raised: %s", backend.id, exc)
            if _all_temps(merged):
                break
        with self._lock:
            self._reading = merged
        return merged

    # -- accessors ------------------------------------------------------
    def snapshot(self) -> SensorReading:
        with self._lock:
            return self._reading

    def active_backend_id(self) -> str:
        return self.snapshot().backend_id

    def statuses(self) -> list[BackendStatus]:
        return [backend.status() for backend in self._backends]

    def cpu_temp_c(self) -> float | None:
        return self.snapshot().cpu_temp_c

    def ram_temp_c(self) -> float | None:
        return self.snapshot().ram_temp_c

    def gpu_temp_c(self) -> float | None:
        return self.snapshot().gpu_temp_c

    def ssd_temp_c(self) -> float | None:
        return self.snapshot().ssd_temp_c

    def _log_active(self) -> None:
        r = self.snapshot()
        LOGGER.info(
            "sensors: active backend=%s cpu=%s ram=%s gpu=%s ssd=%s",
            r.backend_id,
            r.cpu_temp_c is not None,
            r.ram_temp_c is not None,
            r.gpu_temp_c is not None,
            r.ssd_temp_c is not None,
        )


def _all_temps(reading: SensorReading) -> bool:
    return all(
        v is not None
        for v in (reading.cpu_temp_c, reading.ram_temp_c, reading.gpu_temp_c, reading.ssd_temp_c)
    )


_HUB: SensorHub | None = None


def get_hub() -> SensorHub:
    """Return the process-wide SensorHub singleton."""
    global _HUB
    if _HUB is None:
        _HUB = SensorHub()
    return _HUB

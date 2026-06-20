"""LibreHardwareMonitor HTTP backend.

LibreHardwareMonitor (when run with its web server enabled) exposes a JSON
sensor tree at ``http://127.0.0.1:8085/data.json``. This backend reads CPU, RAM,
GPU, and SSD temperatures from that tree. It is the fallback used when the
embedded CLR backend is unavailable but a user is already running LHM.

The HTTP GET is synchronous: the SensorHub calls ``read()`` from its own
background thread, so a blocking fetch never touches the UI thread. A short
back-off skips fetches after repeated failures so a closed port is not hammered.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.request

from services.sensors.backend import BackendStatus
from services.sensors.models import SensorReading

LOGGER = logging.getLogger(__name__)

_URL = "http://127.0.0.1:8085/data.json"
_HTTP_TIMEOUT = 0.5
_FAIL_BACKOFF_MAX = 60.0
_OK_RETRY = 0.0

# Hints are deliberately narrow so a generic "Temperature" sensor name does not
# match more than one component. CPU/GPU/SSD are gated mainly by parent hardware
# name; RAM is gated by the memory hardware parent.
_CPU_NAME_HINTS = ("CPU PACKAGE", "CORE AVERAGE", "CORE MAX", "CPU CORE")
_CPU_PARENT_HINTS = ("INTEL", "RYZEN", "CPU", "CORE I")
_RAM_NAME_HINTS = ("DDR", "DIMM")
_RAM_PARENT_HINTS = ("MEMORY", "CORSAIR", "DIMM", "DDR")
_GPU_NAME_HINTS = ("GPU CORE", "GPU HOT SPOT")
_GPU_PARENT_HINTS = ("NVIDIA", "RADEON", "GEFORCE", "GPU")
_SSD_NAME_HINTS = ("DRIVE TEMPERATURE",)
_SSD_PARENT_HINTS = ("CT4000", "CRUCIAL", "SAMSUNG", "NVME", "SSD", "STORAGE", "WD")


def collect_lhm_sensors(
    data: dict,
    sensor_type: str,
    name_hints: tuple[str, ...],
    parent_hints: tuple[str, ...],
    current_parent: str = "",
) -> list[float]:
    """Walk an LHM JSON node and return matching numeric sensor values."""
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
            collect_lhm_sensors(child, sensor_type, name_hints, parent_hints, next_parent)
        )
    return temps


def _first(values: list[float]) -> float | None:
    return values[0] if values else None


def _avg(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


class LhmHttpBackend:
    """Read CPU/RAM/GPU/SSD temperatures from the LHM HTTP JSON tree."""

    id = "lhm-http"

    def __init__(self) -> None:
        self._last_ok = False
        self._failures = 0
        self._next_attempt = 0.0
        self._detail = "not polled yet"

    def available(self) -> bool:
        return self._last_ok

    def status(self) -> BackendStatus:
        return BackendStatus(self.id, self._last_ok, self._detail)

    def close(self) -> None:
        return None

    def read(self) -> SensorReading:
        now = time.monotonic()
        if now < self._next_attempt:
            return SensorReading(taken_at=now)
        try:
            request = urllib.request.Request(_URL, method="GET")
            with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as resp:
                data = json.loads(resp.read().decode())
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._last_ok = False
            self._failures += 1
            self._detail = f"http fetch failed: {exc}"
            backoff = min(_FAIL_BACKOFF_MAX, 2.0 * (2 ** min(5, self._failures)))
            self._next_attempt = now + backoff
            return SensorReading(taken_at=now)

        self._failures = 0
        self._next_attempt = now + _OK_RETRY
        reading = self.parse(data, now)
        self._last_ok = reading.has_any_temp()
        self._detail = "ok" if self._last_ok else "connected, no matching sensors"
        return reading

    def parse(self, data: dict, taken_at: float) -> SensorReading:
        """Parse an LHM JSON document into a SensorReading (pure; unit-tested)."""
        cpu = collect_lhm_sensors(data, "Temperature", _CPU_NAME_HINTS, _CPU_PARENT_HINTS)
        ram = collect_lhm_sensors(data, "Temperature", _RAM_NAME_HINTS, _RAM_PARENT_HINTS)
        gpu = collect_lhm_sensors(data, "Temperature", _GPU_NAME_HINTS, _GPU_PARENT_HINTS)
        ssd = collect_lhm_sensors(data, "Temperature", _SSD_NAME_HINTS, _SSD_PARENT_HINTS)
        return SensorReading(
            taken_at=taken_at,
            cpu_temp_c=_first(cpu),
            ram_temp_c=_avg(ram),
            gpu_temp_c=_first(gpu),
            ssd_temp_c=_first(ssd),
            backend_id=self.id,
        )

"""Embedded LibreHardwareMonitorLib CLR sensor backend.

Reads CPU, RAM, GPU, and SSD temperatures in-process from the bundled DLL via
pythonnet — no external LibreHardwareMonitor process required. When the DLL or
.NET runtime is absent the backend reports unavailable and the resolver falls
back to the HTTP/NVML/PDH backends.
"""

from __future__ import annotations

import logging
import time

from services.sensors.backend import BackendStatus
from services.sensors.lhm_clr_loader import close_computer, load_computer
from services.sensors.models import SensorReading
from services.sensors.storage_temp import read_ssd_temp

LOGGER = logging.getLogger(__name__)

_CPU_TYPES = ("Cpu",)
_RAM_TYPES = ("Memory",)
_GPU_TYPES = ("GpuNvidia", "GpuAmd", "GpuIntel")


def _temp_sensors(hardware: object) -> list[tuple[str, float]]:
    out: list[tuple[str, float]] = []
    for sensor in getattr(hardware, "Sensors", []):
        if str(getattr(sensor, "SensorType", "")) != "Temperature":
            continue
        value = getattr(sensor, "Value", None)
        if value is None:
            continue
        out.append((str(getattr(sensor, "Name", "")).upper(), float(value)))
    return out


def _pick_cpu(sensors: list[tuple[str, float]]) -> float | None:
    for name, value in sensors:
        if "PACKAGE" in name or "CORE AVERAGE" in name:
            return value
    return sensors[0][1] if sensors else None


def _avg(values: list[float]) -> float | None:
    return (sum(values) / len(values)) if values else None


class LhmClrBackend:
    """In-process LHM CLR backend for CPU/RAM/GPU/SSD temperatures."""

    id = "lhm-clr"

    def __init__(self) -> None:
        self._computer: object | None = None
        self._tried = False
        self._last_ok = False
        self._detail = "not initialized"

    def _ensure(self) -> None:
        if self._tried:
            return
        self._tried = True
        self._computer = load_computer()
        if self._computer is None:
            self._detail = "dll or .NET runtime unavailable"

    def available(self) -> bool:
        self._ensure()
        return self._computer is not None

    def status(self) -> BackendStatus:
        return BackendStatus(self.id, self._last_ok, self._detail)

    def close(self) -> None:
        close_computer(self._computer)
        self._computer = None

    def read(self) -> SensorReading:
        now = time.monotonic()
        self._ensure()
        if self._computer is None:
            return SensorReading(taken_at=now)

        cpu: float | None = None
        ram: list[float] = []
        gpu: float | None = None
        try:
            for hardware in getattr(self._computer, "Hardware", []):
                htype = str(getattr(hardware, "HardwareType", ""))
                if htype not in _CPU_TYPES + _RAM_TYPES + _GPU_TYPES:
                    continue
                hardware.Update()
                sensors = _temp_sensors(hardware)
                if htype in _CPU_TYPES and cpu is None:
                    cpu = _pick_cpu(sensors)
                elif htype in _RAM_TYPES:
                    ram.extend(v for _n, v in sensors)
                elif htype in _GPU_TYPES and gpu is None and sensors:
                    gpu = sensors[0][1]
            ssd = read_ssd_temp(self._computer)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._last_ok = False
            self._detail = f"read failed: {exc}"
            LOGGER.debug("sensors: clr read failed: %s", exc)
            return SensorReading(taken_at=now)

        reading = SensorReading(
            taken_at=now,
            cpu_temp_c=cpu,
            ram_temp_c=_avg(ram),
            gpu_temp_c=gpu,
            ssd_temp_c=ssd,
            backend_id=self.id,
        )
        self._last_ok = reading.has_any_temp()
        self._detail = "ok" if self._last_ok else "opened, no temperatures yet"
        return reading

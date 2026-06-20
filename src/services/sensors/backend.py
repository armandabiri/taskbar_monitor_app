"""Sensor backend protocol and status shared by every concrete backend.

A backend reads hardware temperatures from one source (the embedded CLR library,
the LibreHardwareMonitor HTTP server, NVML, or PDH). The resolver probes
backends through ``available()`` and reads them through ``read()``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from services.sensors.models import SensorReading


@dataclass(frozen=True)
class BackendStatus:
    """Why a backend is or is not usable, surfaced by the diagnostics dialog."""

    backend_id: str
    available: bool
    detail: str = ""


@runtime_checkable
class SensorBackend(Protocol):
    """A source of hardware temperatures."""

    id: str

    def available(self) -> bool:
        """Return True when this backend can currently read sensors."""
        ...

    def read(self) -> SensorReading:
        """Return the current reading; unreadable fields are ``None``."""
        ...

    def close(self) -> None:
        """Release any held resources. Safe to call repeatedly."""
        ...

    def status(self) -> BackendStatus:
        """Return a human-facing availability summary."""
        ...

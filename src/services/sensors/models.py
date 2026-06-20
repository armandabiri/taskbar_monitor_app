"""Typed hardware-sensor reading model shared by every sensor backend.

A ``SensorReading`` is an immutable snapshot of the temperatures (and a few
related scalars) collected from whichever backend is currently active. Every
field is optional: a backend that cannot read a given sensor leaves it ``None``
and the UI renders ``N/A`` for that trace.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class SensorKind(Enum):
    """The hardware scalars a backend can report."""

    CPU_TEMP = "cpu_temp_c"
    RAM_TEMP = "ram_temp_c"
    GPU_TEMP = "gpu_temp_c"
    SSD_TEMP = "ssd_temp_c"
    GPU_UTIL = "gpu_util_percent"
    VRAM_PERCENT = "vram_percent"


@dataclass(frozen=True)
class SensorReading:
    """An immutable snapshot of the current sensor scalars.

    ``taken_at`` is a monotonic timestamp (seconds). Temperatures are in Celsius.
    ``backend_id`` names the backend that produced the reading (``"none"`` when
    no backend is available).
    """

    taken_at: float = 0.0
    cpu_temp_c: float | None = None
    ram_temp_c: float | None = None
    gpu_temp_c: float | None = None
    ssd_temp_c: float | None = None
    gpu_util_percent: float | None = None
    vram_percent: float | None = None
    backend_id: str = "none"

    def value(self, kind: SensorKind) -> float | None:
        """Return the scalar for ``kind``, or ``None`` when unpopulated."""
        if not isinstance(kind, SensorKind):
            raise ValueError(f"unknown sensor kind: {kind!r}")
        return getattr(self, kind.value)

    def merged_with(self, other: SensorReading) -> SensorReading:
        """Return a reading where ``other``'s populated fields fill this one's gaps.

        Used by the resolver to let a fallback backend supply sensors the
        primary backend could not read.
        """
        return SensorReading(
            taken_at=max(self.taken_at, other.taken_at),
            cpu_temp_c=self.cpu_temp_c if self.cpu_temp_c is not None else other.cpu_temp_c,
            ram_temp_c=self.ram_temp_c if self.ram_temp_c is not None else other.ram_temp_c,
            gpu_temp_c=self.gpu_temp_c if self.gpu_temp_c is not None else other.gpu_temp_c,
            ssd_temp_c=self.ssd_temp_c if self.ssd_temp_c is not None else other.ssd_temp_c,
            gpu_util_percent=(
                self.gpu_util_percent
                if self.gpu_util_percent is not None
                else other.gpu_util_percent
            ),
            vram_percent=(
                self.vram_percent if self.vram_percent is not None else other.vram_percent
            ),
            backend_id=self.backend_id if self.backend_id != "none" else other.backend_id,
        )

    def has_any_temp(self) -> bool:
        """Return True when at least one temperature field is populated."""
        return any(
            v is not None
            for v in (self.cpu_temp_c, self.ram_temp_c, self.gpu_temp_c, self.ssd_temp_c)
        )

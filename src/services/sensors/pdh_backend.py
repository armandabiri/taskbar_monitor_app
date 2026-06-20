"""Windows PDH thermal-zone CPU temperature backend.

Reads ``\\Thermal Zone Information(*)\\Temperature`` via the Performance Data
Helper API. This is the last-resort CPU temperature source: many machines only
expose a static fake ACPI value (301.0 K), which is discarded here.
"""

from __future__ import annotations

import logging
import time

from services.sensors.backend import BackendStatus
from services.sensors.models import SensorReading

LOGGER = logging.getLogger(__name__)

_FAKE_ACPI_KELVIN = 301.0


class PdhBackend:
    """CPU temperature via the Windows PDH thermal-zone counter."""

    id = "pdh"

    def __init__(self) -> None:
        self._ready = False
        self._query = None
        self._counter = None
        self._last_ok = False
        self._detail = "not initialized"

    def _ensure_init(self) -> None:
        if self._ready:
            return
        try:
            import ctypes
            from ctypes import wintypes

            pdh = ctypes.windll.pdh
            query = wintypes.HANDLE()
            pdh.PdhOpenQueryW(None, 0, ctypes.byref(query))
            counter = wintypes.HANDLE()
            res = pdh.PdhAddEnglishCounterW(
                query, r"\Thermal Zone Information(*)\Temperature", 0, ctypes.byref(counter)
            )
            if res == 0:
                self._query = query
                self._counter = counter
                self._ready = True
            else:
                self._detail = f"PdhAddEnglishCounterW failed: {res}"
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._detail = f"pdh init failed: {exc}"
            LOGGER.debug("PDH initialization failed: %s", exc)

    def _read_kelvin(self) -> float | None:
        self._ensure_init()
        if not self._ready:
            return None
        try:
            import ctypes
            from ctypes import wintypes

            pdh = ctypes.windll.pdh
            pdh.PdhCollectQueryData(self._query)

            class _CounterValue(ctypes.Structure):
                _fields_ = [("CStatus", wintypes.DWORD), ("doubleValue", ctypes.c_double)]

            val = _CounterValue()
            res = pdh.PdhGetFormattedCounterValue(self._counter, 0x200, None, ctypes.byref(val))
            if res == 0:
                return val.doubleValue
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self._detail = f"pdh collect failed: {exc}"
            LOGGER.debug("PDH collect failed: %s", exc)
        return None

    def available(self) -> bool:
        return self._last_ok

    def status(self) -> BackendStatus:
        return BackendStatus(self.id, self._last_ok, self._detail)

    def close(self) -> None:
        return None

    def read(self) -> SensorReading:
        now = time.monotonic()
        kelvin = self._read_kelvin()
        if kelvin is None or kelvin == _FAKE_ACPI_KELVIN:
            self._last_ok = False
            if kelvin == _FAKE_ACPI_KELVIN:
                self._detail = "only static ACPI thermal zone present"
            return SensorReading(taken_at=now)
        self._last_ok = True
        self._detail = "ok"
        return SensorReading(taken_at=now, cpu_temp_c=kelvin - 273.15, backend_id=self.id)

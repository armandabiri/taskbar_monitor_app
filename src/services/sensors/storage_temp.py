"""NVMe/SSD temperature extraction from an LHM ``Computer``.

Selects the system drive's temperature sensor, preferring the configured NVMe
model (Crucial T700 ``CT4000T700SSD3``) by name, then falling back to the first
storage device that reports a temperature.
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)

# Preferred drive identity from the deployment (Crucial T700 4TB NVMe).
PREFERRED_DRIVE_HINTS = ("CT4000T700SSD3", "T700", "CRUCIAL")


def _hardware_temp(hardware: object) -> float | None:
    """Return the first Temperature sensor value on an LHM hardware node."""
    best: float | None = None
    for sensor in getattr(hardware, "Sensors", []):
        if str(getattr(sensor, "SensorType", "")) != "Temperature":
            continue
        value = getattr(sensor, "Value", None)
        if value is None:
            continue
        name = str(getattr(sensor, "Name", "")).upper()
        # A drive's main temperature is usually the highest-priority "Temperature"
        # sensor; prefer an explicit drive-temperature name when present.
        if "DRIVE" in name or best is None:
            best = float(value)
    return best


def read_ssd_temp(computer: object | None) -> float | None:
    """Return the system SSD/NVMe temperature in Celsius, or None.

    ``computer`` is an opened LHM ``Computer`` (or any object exposing
    ``Hardware`` with ``HardwareType``/``Name``/``Sensors``). Storage hardware is
    matched first by the preferred drive name, then by first-with-temperature.
    """
    if computer is None:
        return None
    preferred: float | None = None
    fallback: float | None = None
    for hardware in getattr(computer, "Hardware", []):
        if str(getattr(hardware, "HardwareType", "")) != "Storage":
            continue
        try:
            hardware.Update()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.debug("sensors: storage update failed: %s", exc)
        temp = _hardware_temp(hardware)
        if temp is None:
            continue
        name = str(getattr(hardware, "Name", "")).upper()
        if any(hint in name for hint in PREFERRED_DRIVE_HINTS):
            preferred = temp
        elif fallback is None:
            fallback = temp
    return preferred if preferred is not None else fallback

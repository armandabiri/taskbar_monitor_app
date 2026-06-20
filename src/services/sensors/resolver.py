"""Ordered sensor-backend resolution.

Builds the prioritized list of backends the SensorHub reads each cycle. The
default ``auto`` source prefers the embedded CLR backend, then the LHM HTTP
server, then NVML, then PDH. Backends that are structurally unavailable (the CLR
DLL or pythonnet is absent, or pynvml is not installed) are dropped so the hub
never wastes a cycle on them; the always-probeable HTTP and PDH backends are
always retained.
"""

from __future__ import annotations

import logging

from services.sensors.backend import SensorBackend
from services.sensors.lhm_clr_backend import LhmClrBackend
from services.sensors.lhm_http_backend import LhmHttpBackend
from services.sensors.nvml_backend import NvmlBackend
from services.sensors.pdh_backend import PdhBackend

LOGGER = logging.getLogger(__name__)

# Backends whose read() self-determines availability each cycle and so are always
# kept in the auto chain even before their first successful read.
_ALWAYS_KEEP = ("lhm-http", "pdh")

VALID_SOURCES = ("auto", "clr", "http")


def resolve(source: str = "auto") -> list[SensorBackend]:
    """Return the ordered backends for ``source``."""
    if source == "clr":
        return [LhmClrBackend()]
    if source == "http":
        return [LhmHttpBackend()]
    candidates: list[SensorBackend] = [
        LhmClrBackend(),
        LhmHttpBackend(),
        NvmlBackend(),
        PdhBackend(),
    ]
    kept: list[SensorBackend] = []
    for backend in candidates:
        if backend.id in _ALWAYS_KEEP or backend.available():
            kept.append(backend)
        else:
            LOGGER.debug("sensors: dropping unavailable backend %s", backend.id)
    return kept

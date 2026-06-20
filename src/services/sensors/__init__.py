"""In-process hardware sensor package: typed readings, backends, and the hub.

Public surface:
    SensorKind, SensorReading  - the typed reading model
    SensorHub, get_hub         - the aggregating hub the UI reads
    BackendStatus              - per-backend availability for diagnostics
"""

from services.sensors.backend import BackendStatus
from services.sensors.hub import SensorHub, get_hub
from services.sensors.models import SensorKind, SensorReading

__all__ = [
    "BackendStatus",
    "SensorHub",
    "SensorKind",
    "SensorReading",
    "get_hub",
]

"""Append-only sensor telemetry logging with bounded retention.

Writes each ``SensorReading`` to a CSV or JSONL file under the writable app-data
directory so users can review thermal/load history after the on-screen ring
buffer scrolls away. Retention trims the file to a bounded number of rows.
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque

from services.sensors.models import SensorReading

LOGGER = logging.getLogger(__name__)

CSV_HEADER = "taken_at,cpu_temp_c,ram_temp_c,gpu_temp_c,ssd_temp_c,backend_id"
_FIELDS = ("taken_at", "cpu_temp_c", "ram_temp_c", "gpu_temp_c", "ssd_temp_c", "backend_id")


def _cell(value: object) -> str:
    return "" if value is None else str(value)


class TelemetryLog:
    """Persist sensor readings to ``path`` as CSV or JSONL with row retention."""

    def __init__(self, path: str, fmt: str = "csv", retention_rows: int = 50000) -> None:
        self._path = path
        self._fmt = "jsonl" if fmt == "jsonl" else "csv"
        self._retention = max(1, int(retention_rows))
        self._disabled = False

    @property
    def path(self) -> str:
        return self._path

    def _row(self, reading: SensorReading) -> str:
        if self._fmt == "jsonl":
            return json.dumps({f: getattr(reading, f) for f in _FIELDS})
        return ",".join(_cell(getattr(reading, f)) for f in _FIELDS)

    def append(self, reading: SensorReading) -> None:
        """Append one reading; create the file/header and enforce retention."""
        if self._disabled:
            return
        try:
            os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
            new_file = not os.path.exists(self._path)
            with open(self._path, "a", encoding="utf-8") as handle:
                if new_file and self._fmt == "csv":
                    handle.write(CSV_HEADER + "\n")
                handle.write(self._row(reading) + "\n")
            self._trim()
        except OSError as exc:
            LOGGER.warning("telemetry append failed; disabling for session: %s", exc)
            self._disabled = True

    def _trim(self) -> None:
        with open(self._path, encoding="utf-8") as handle:
            lines = handle.readlines()
        header: list[str] = []
        body = lines
        if self._fmt == "csv" and lines and lines[0].startswith("taken_at"):
            header = [lines[0]]
            body = lines[1:]
        if len(body) <= self._retention:
            return
        trimmed = list(deque(body, maxlen=self._retention))
        with open(self._path, "w", encoding="utf-8") as handle:
            handle.writelines(header + trimmed)

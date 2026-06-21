"""Append-only sensor telemetry logging with bounded retention.

Writes each ``SensorReading`` to a CSV or JSONL file under the writable app-data
directory so users can review thermal/load history after the on-screen ring
buffer scrolls away. When the row count reaches ``retention_rows`` the file is
rotated (renamed to ``<path>.bak``) and a fresh file is opened — no whole-file
read is needed on the hot append path.
"""

from __future__ import annotations

import json
import logging
import os
from typing import IO

from services.sensors.models import SensorReading

LOGGER = logging.getLogger(__name__)

CSV_HEADER = "taken_at,cpu_temp_c,ram_temp_c,gpu_temp_c,ssd_temp_c,backend_id"
_FIELDS = ("taken_at", "cpu_temp_c", "ram_temp_c", "gpu_temp_c", "ssd_temp_c", "backend_id")


def _cell(value: object) -> str:
    return "" if value is None else str(value)


class TelemetryLog:
    """Persist sensor readings to ``path`` as CSV or JSONL with row retention.

    Keeps one open append handle for the session; tracks the row count in memory
    so no full-file read occurs on the hot path.  When ``row_count`` reaches
    ``retention_rows`` the file is rotated: renamed to ``<path>.bak`` and a
    fresh file opened.
    """

    def __init__(self, path: str, fmt: str = "csv", retention_rows: int = 50000) -> None:
        self._path = path
        self._fmt = "jsonl" if fmt == "jsonl" else "csv"
        self._retention = max(1, int(retention_rows))
        self._disabled = False
        self._handle: IO[str] | None = None
        self._row_count = 0

    @property
    def path(self) -> str:
        return self._path

    def _row(self, reading: SensorReading) -> str:
        if self._fmt == "jsonl":
            return json.dumps({f: getattr(reading, f) for f in _FIELDS})
        return ",".join(_cell(getattr(reading, f)) for f in _FIELDS)

    def _open_handle(self) -> None:
        """Open the append handle; count existing rows once so rotation is correct."""
        os.makedirs(os.path.dirname(self._path) or ".", exist_ok=True)
        new_file = not os.path.exists(self._path)
        if new_file:
            self._row_count = 0
        else:
            with open(self._path, encoding="utf-8") as f:
                total = sum(1 for _ in f)
            self._row_count = max(0, total - 1) if self._fmt == "csv" else total
        self._handle = open(self._path, "a", encoding="utf-8")  # noqa: WPS515
        if new_file and self._fmt == "csv":
            self._handle.write(CSV_HEADER + "\n")
            self._handle.flush()

    def append(self, reading: SensorReading) -> None:
        """Append one reading; rotate when the row bound is reached."""
        if self._disabled:
            return
        try:
            if self._handle is None:
                self._open_handle()
            self._handle.write(self._row(reading) + "\n")
            self._handle.flush()
            self._row_count += 1
            if self._row_count >= self._retention:
                self._rotate()
        except OSError as exc:
            LOGGER.warning("telemetry append failed; disabling for session: %s", exc)
            self._disabled = True

    def _rotate(self) -> None:
        """Close current file, rename to ``.bak``, open a fresh file."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None
        try:
            os.replace(self._path, self._path + ".bak")
        except OSError:
            pass
        self._row_count = 0
        self._handle = open(self._path, "a", encoding="utf-8")  # noqa: WPS515
        if self._fmt == "csv":
            self._handle.write(CSV_HEADER + "\n")
            self._handle.flush()

    def close(self) -> None:
        """Flush and close the append handle."""
        if self._handle is not None:
            self._handle.close()
            self._handle = None

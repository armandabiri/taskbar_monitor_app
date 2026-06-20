"""Tests for telemetry logging format and retention."""

from __future__ import annotations

import json

from services.sensors.models import SensorReading
from services.sensors.telemetry_log import CSV_HEADER, TelemetryLog


def test_csv_writes_header_and_row(tmp_path):
    path = tmp_path / "t.csv"
    log = TelemetryLog(str(path), fmt="csv")
    log.append(SensorReading(taken_at=1.0, cpu_temp_c=50.0, ssd_temp_c=45.0, backend_id="x"))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == CSV_HEADER
    assert lines[1].startswith("1.0,50.0,")
    assert lines[1].endswith(",x")


def test_jsonl_writes_object(tmp_path):
    path = tmp_path / "t.jsonl"
    log = TelemetryLog(str(path), fmt="jsonl")
    log.append(SensorReading(taken_at=2.0, gpu_temp_c=61.0, backend_id="nvml"))
    obj = json.loads(path.read_text(encoding="utf-8").splitlines()[0])
    assert obj["gpu_temp_c"] == 61.0
    assert obj["backend_id"] == "nvml"


def test_retention_trims_to_bound(tmp_path):
    path = tmp_path / "t.csv"
    log = TelemetryLog(str(path), fmt="csv", retention_rows=3)
    for i in range(10):
        log.append(SensorReading(taken_at=float(i), cpu_temp_c=float(i)))
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == CSV_HEADER
    assert len(lines) == 1 + 3  # header + 3 retained rows
    assert lines[-1].startswith("9.0,")

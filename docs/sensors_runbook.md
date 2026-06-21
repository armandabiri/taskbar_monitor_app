# Hardware Sensors Runbook

How temperature monitoring works in Taskbar Monitor, and how to operate and
troubleshoot it.

## Architecture

Temperature reads live in the `services.sensors` package:

- `models.py` — `SensorKind` and the immutable `SensorReading` snapshot.
- `backend.py` — the `SensorBackend` protocol and `BackendStatus`.
- `lhm_clr_backend.py` + `lhm_clr_loader.py` — the embedded
  `LibreHardwareMonitorLib.dll` loaded in-process through pythonnet.
- `lhm_http_backend.py` — the LibreHardwareMonitor HTTP JSON fallback
  (`http://127.0.0.1:8085/data.json`).
- `nvml_backend.py` — NVIDIA NVML GPU temperature/utilization/VRAM.
- `pdh_backend.py` — Windows PDH thermal-zone CPU temperature (last resort).
- `storage_temp.py` — NVMe/SSD temperature selection (prefers `CT4000T700SSD3`).
- `resolver.py` — builds the ordered backend chain for the configured source.
- `hub.py` — `SensorHub`: a background thread that refreshes the chain every 2 s
  and caches a merged reading; the UI thread only reads the cache.
- `thermal_alerts.py` — per-sensor threshold evaluation with debounce/cooldown.
- `telemetry_log.py` — append-only CSV/JSONL logging with row retention.

The `ui.scope_manager.ScopeManager` translates each cached reading into the
oscilloscope scopes (`temp`, `gputemp`, `ssdtemp`), thermal alerts, and telemetry.

## Sampler architecture

System metrics (CPU, RAM, network, disk, top processes) are collected by
`SystemSampler`, a `QObject` that runs on a dedicated daemon worker thread.

- The UI thread never calls `psutil` directly; it receives `SystemSnapshot`
  objects via a cross-thread `snapshot_ready` signal.
- Top processes are refreshed at a separate `top_proc_refresh_s` cadence (2 s)
  so the process popup stays up-to-date without extra polling.
- The process popup subscribes to `snapshot_ready` on show and unsubscribes on
  hide, so it consumes no CPU when hidden.

### Adaptive cadence

The sampler interval adapts to the app's visibility:

| QSettings key | Default | Meaning |
| --- | --- | --- |
| `sampler/active_interval_ms` | 1000 | Poll rate when the widget is visible. |
| `sampler/hidden_interval_ms` | 5000 | Poll rate when the widget is hidden/minimised. |
| `sampler/pause_on_battery` | false | Stretch to `hidden_interval_ms` whenever on battery power. |

Edit these in **Monitor Settings…** (Cadence section).

## Shutdown hygiene

`MonitorLifecycle` holds an ordered registry of stop callbacks (sampler → topmost
controller → sensor hub). On close, `shutdown()` signals each component to stop,
then joins its worker thread with a bounded timeout. The sensor hub signals its
refresh thread, closes all backends, and calls `nvml_shutdown()` exactly once, so
NVML handles are released before the process exits.

## App Footprint (self-overhead panel)

**App Footprint…** (app menu) opens a read-only dialog showing the monitor's own
CPU usage and RSS memory via `AppMetricsProbe.sample()`, alongside the last
cleanup-history entry so you can see how much overhead a cleanup consumed.

## Backend selection (`sensors/source`)

Set in **Monitor Settings…**. Values:

- `auto` (default) — try embedded CLR, then HTTP, then NVML, then PDH; merge so the
  highest-priority backend that reports each sensor wins and lower ones fill gaps.
- `clr` — use only the embedded `LibreHardwareMonitorLib.dll`.
- `http` — use only a running LibreHardwareMonitor with its web server enabled.

The active backend appears at startup in the log:
`sensors: active backend=<id> cpu=<bool> ram=<bool> gpu=<bool> ssd=<bool>`, and live
in **Sensor Diagnostics…**.

## Temperature unit

`sensors/temp_unit` (`C` default, or `F`), set in **Monitor Settings…**, controls
the on-scope display unit. Readings are stored and compared internally in Celsius
(so thresholds stay in °C); only the displayed labels convert to °F.

## Thresholds and alerts

Per-sensor Celsius thresholds (defaults): CPU 95, RAM 70, GPU 90, SSD 80. When a
sensor stays at/above its threshold past a short debounce, a toast fires (then a
cooldown) and the breached scope's trace turns red. Toggle alerting and edit
thresholds in **Monitor Settings…** (`sensors/alerts_enabled`,
`sensors/threshold_*_c`).

## Telemetry export

Enable in **Monitor Settings…** (`telemetry/enabled`). Readings append to
`sensor_telemetry.csv` (or `.jsonl`) under the app-data directory. Columns:
`taken_at,cpu_temp_c,ram_temp_c,gpu_temp_c,ssd_temp_c,backend_id`. The file is
trimmed to `telemetry/retention_rows` (default 50000).

The writer keeps the file handle open between writes (no per-row open/close) and
rotates via `os.replace` on a `.bak` so the header is always preserved. The hot
path never reads the whole file.

## Refreshing / pinning the bundled DLL

```powershell
python scripts/fetch_sensor_dll.py --download   # fetch the pinned release into src/assets/sensors/
python scripts/fetch_sensor_dll.py --verify     # confirm presence + checksum
```

`--verify` exits `0` on success, `1` when the DLL is missing, `2` on checksum
mismatch. After a trusted first download, set `EXPECTED_SHA256` in
`scripts/fetch_sensor_dll.py` and `src/services/sensors/lhm_clr_loader.py` to pin
the file. The build script (`scripts/build_exe.ps1`) runs `--download` then
`--verify` before PyInstaller bundles the DLL via `TaskbarMonitor.spec`.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| All temps read `N/A` | No backend available | Check the startup log; ensure the DLL is present (`fetch_sensor_dll.py --verify`) and pythonnet is installed |
| SSD temp `N/A`, others OK | Drive temp needs elevated access | Run the app as administrator |
| `clr backend unavailable: ...` in log | Missing DLL or .NET runtime | Install the .NET runtime; re-fetch the DLL; the app falls back to HTTP/NVML/PDH meanwhile |
| Wrong/zero GPU temp | NVML unavailable (non-NVIDIA or driver) | Use the CLR backend, which reads GPU temp directly |

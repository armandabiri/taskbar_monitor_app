# Taskbar Monitor App

Taskbar Monitor is a Windows 11 desktop utility built with PyQt6. It combines a compact always-on-top taskbar monitor with resource cleanup actions, process snapshots, clipboard history, and native toast notifications.

## Features

- Live CPU, RAM, network, disk, GPU, VRAM, temperature, battery, and timer widgets.
- Profile-driven cleanup modes for trim, throttle, and optional kill actions.
- Process snapshots stored as CSV under the writable app-data directory.
- Snapshot extra-process cleanup preview:
  - compare a saved snapshot to the live system
  - preview only the extra live processes
  - background extras are selected by default
  - visible-window and tray-icon extras are shown but unchecked by default
- Cleanup history dialog with bounded local retention.
- Cleanup result dialog for zero-action and partial-failure runs.

## Runtime Files

The app now stores writable runtime files under `QStandardPaths.AppDataLocation`.

- Snapshots: `.../snapshots`
- Cleanup history: `.../cleanup_history.jsonl`
- Log file: `.../taskbar_monitor.log`

## Snapshot Extras Workflow

1. Open `Process Snapshots...`.
2. Take a baseline snapshot before opening extra apps.
3. Later, click `Kill extras...` on that snapshot.
4. Review the live extra-process preview.
5. Approve the exact extra PIDs to terminate.

This flow does not depend on memory-pressure thresholds. It targets only the extra processes that appeared after the baseline snapshot.

## Development

Install the project and dev tooling:

```powershell
python -m pip install -e .[dev]
```

Run validation:

```powershell
python -m pytest tests -q
python -m ruff check src tests
pyright
```

## Build

Build the Windows executable with:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/build_exe.ps1
```

The packaged executable is written to `dist/TaskbarMonitor.exe`.

# Taskbar Monitor App

Taskbar Monitor is a Windows 11 desktop utility built with PyQt6. It combines a compact always-on-top taskbar monitor with resource cleanup actions, process snapshots, clipboard history, and native toast notifications.

## Features

- Live CPU, RAM, network, disk, GPU, VRAM, battery, and timer widgets, plus
  dedicated CPU/RAM, GPU, and SSD/NVMe **temperature** oscilloscopes (°C).
- One-click microphone recording to MP3 with shared input access.
- Recording settings from the app menu: save folder, filename prefix, bitrate, sample rate, channel mode, and optional auto-open folder after save.
- Profile-driven cleanup modes for trim, throttle, and optional kill actions.
- **Force Reclaim Now** — run a full cleanup pass even when memory is below the
  pressure threshold, so the button always does visible work.
- **Live progress + cancel** — a modeless overlay shows the current phase (scan,
  trim, throttle, kill, flush) and lets you cancel an in-flight run.
- **Preview cleanup** — a dry run that scans and scores without acting, lists the
  ranked candidates and estimated reclaim, then offers *Run now* / *Run forced*.
- **Plain-language results** — the result dialog explains *why* a run cleaned
  nothing (e.g. memory below threshold) and offers a one-click *Force a full pass*.
- **Measured reclaim** — the result reports the real available-RAM delta after a
  short settle, alongside the per-trim estimate.
- **Auto-clean watchdog** — opt-in; fires a forced Smart cleanup when RAM stays
  under pressure for a debounce window, then respects a cooldown (off by default).
- **Reset throttled processes** — restore priority/affinity of processes a cleanup
  throttled.
- **Flush standby cache** — purge the Windows standby file cache directly.
- Faster scanning — USS lookups for large processes run on a bounded thread pool.
- Process snapshots stored as CSV under the writable app-data directory.
- Snapshot extra-process cleanup preview:
  - compare a saved snapshot to the live system
  - preview only the extra live processes
  - background extras are selected by default
  - visible-window and tray-icon extras are shown but unchecked by default
- Cleanup history dialog with bounded local retention.
- Cleanup result dialog for zero-action and partial-failure runs.

See [docs/cleanup_runbook.md](docs/cleanup_runbook.md) for how the cleanup
actions behave and how to fix a "nothing happened" run.

## Screenshot tools

Open the **Screenshot** submenu from the app menu, or use the global hotkeys below. A
FastStone-style floating toolbar (menu → **Show Capture Toolbar**) gives one-click access
to the common modes and can be dragged anywhere on screen.

Capture modes:

- **Region** — drag a rectangle on any monitor.
- **Element (smart)** — hover-highlight UI Automation elements and click one to capture it.
- **Repeat last region** — re-capture the previously selected rectangle.
- **Full screen** — capture the monitor under the cursor.
- **Whole desktop** — stitch every monitor into one wide image (multi-monitor).
- **Active window** — capture the foreground window (clean even when partially covered).
- **Scrolling window** — click a scrollable pane; the app scrolls to the top, then captures
  and stitches frames top-to-bottom into one tall image.

Post-capture and output:

- **Clipboard and/or file** — every capture can be copied, saved, or both, per the settings.
- **Editor** — enable *Open the editor after each capture* to annotate (arrow, text,
  rectangle, blur) and crop before the image is delivered.
- **Pin to screen** — pin the last capture as an always-on-top overlay (drag to move,
  right-click for opacity/copy/close, `Esc` to dismiss).
- **Delay** — set a 1–10 s countdown before a capture runs (useful for menus and tooltips).
- **Capture collection (multi-image clipboard)** — press **Shift+Win+M** to start a
  collection session; every capture you take while it is active is appended to a stack (a
  badge shows the running count). Press **Shift+Win+M** again to stop. Then focus any app
  that accepts pasted images and press **Shift+Win+V** to paste the whole stack one image
  after another (each is placed on the clipboard and pasted with `Ctrl+V` in order); the
  stack is cleared afterward.

Settings live in **Screenshot Settings…** (QSettings under the `screenshot/` group): save
folder, file format (PNG/JPEG), copy-to-clipboard, save-to-disk, capture delay, auto-open
editor, scroll step delay, and debug frame dumps.

### Hotkeys

Global shortcuts are registered when possible (a failed registration is logged on startup):

| Action | Default chord |
| --- | --- |
| Region capture | Shift+Win+R |
| Element capture | Shift+Win+E |
| Repeat last region | Shift+Win+Alt+R (or Ctrl+Shift+Alt+R) |
| Full screen | Shift+Win+F |
| Whole desktop | Shift+Win+D |
| Active window | Shift+Win+W |
| Scrolling capture | Shift+Win+S |
| Pin last capture | Shift+Win+P |
| Start/stop capture collection | Shift+Win+M |
| Paste collected captures | Shift+Win+V |

### Scrolling capture troubleshooting

- Scrolling capture works best on standard scrollable panes (browsers, editors, document
  views). Some custom surfaces never scroll programmatically and will return a single frame.
- If frames look duplicated or misaligned, increase **Delay between scroll steps** in
  Screenshot Settings so each frame settles before capture.
- For nested scroll areas (IDE sidebars, inner web panes), click directly inside the pane you
  want; the app prefers the smallest scrollable container under the click.
- To diagnose a bad stitch, enable **Write scroll debug frames** (off by default) — raw
  frames, the stitched result, and metrics are written to `.intelag/reports/scroll_live`.

## Hardware sensors

Taskbar Monitor reads CPU, RAM, GPU, and SSD/NVMe temperatures **in-process** from
an embedded copy of the open-source `LibreHardwareMonitorLib.dll` (MPL-2.0) via
[pythonnet](https://pythonnet.github.io/). No separate sensor app needs to run.

- Temperatures show on three scopes: **Temp (CPU/RAM)**, **GPU Temp**, and **SSD
  Temp**, in your chosen unit (°C or °F). Toggle any of them from the right-click
  **Graphs** submenu.
- **Monitor Settings…** (right-click menu) sets the sensor source (`auto` / `clr` /
  `http`), the **temperature unit (°C / °F)**, per-sensor alert thresholds (SSD
  defaults to 80 °C — the Crucial T700 throttle point), thermal-alert toggling, and
  telemetry logging.
- **Sensor Diagnostics…** shows the active backend and per-sensor availability so you
  can tell whether (and why) a reading is `N/A`.
- Backends are tried in order: embedded CLR → LibreHardwareMonitor HTTP
  (`127.0.0.1:8085`) → NVML → PDH. If the embedded DLL or the .NET runtime is
  missing, the app falls back automatically and logs the reason.
- Full coverage (especially SSD/NVMe temperature) may require running the app as
  administrator.

The DLL is fetched and checksum-verified at build time and bundled into the
executable. See [docs/sensors_runbook.md](docs/sensors_runbook.md) for the sensor
source, thresholds, telemetry export, and DLL-refresh workflow.

## Runtime Files

The app now stores writable runtime files under `QStandardPaths.AppDataLocation`.

- Snapshots: `.../snapshots`
- Cleanup history: `.../cleanup_history.jsonl`
- Log file: `.../taskbar_monitor.log`

By default, microphone recordings are written to the user's music library under:

- `.../Music/TaskbarMonitor/Recordings`

You can change the save location from `Microphone Recording -> Settings…` in the app menu.

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

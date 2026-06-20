# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files

sounddevice_datas = collect_data_files('_sounddevice_data')

a = Analysis(
    ['src/main.py'],
    pathex=['src'],
    binaries=[],
    datas=[
        ('src/assets/taskbar-monitor.svg', 'assets'),
        ('src/assets/taskbar-monitor.ico', 'assets'),
        # Embedded open-source sensor backend (fetched by scripts/fetch_sensor_dll.py
        # before the build) so CPU/RAM/GPU/SSD temperatures work with no external tool.
        ('src/assets/sensors/LibreHardwareMonitorLib.dll', 'assets/sensors'),
    ] + sounddevice_datas,
    hiddenimports=[
        'ui.cleanup_history_dialog',
        'ui.cleanup_result_dialog',
        'ui.cleanup_controller',
        'ui.cleanup_menu',
        'ui.cleanup_progress_dialog',
        'ui.cleanup_preview_dialog',
        'ui.auto_clean_settings_dialog',
        'ui.snapshot_live_cleanup_dialog',
        'services.auto_clean_watchdog',
        'services.resource_control.history',
        'services.resource_control.snapshot_scope',
        'services.resource_control.system_reclaim',
        'services.resource_control.system_scan',
        'services.resource_control.snapshot_reclaim',
        'services.resource_control.runner_common',
        'services.resource_control.uss_prefetch',
        'services.uia_service',
        'ui.capture_controller',
        'ui.capture_selectors',
        'ui.capture_collection',
        'ui.capture_delay_overlay',
        'ui.capture_toolbar',
        'ui.pinned_capture_overlay',
        'ui.screenshot_editor_dialog',
        'ui.screenshot_menu',
        'ui.screenshot_settings_dialog',
        'ui.scroll_capture_progress',
        'services.screenshot',
        'services.screenshot.win32_capture',
        'services.screenshot.scroll_coordinator',
        'services.screenshot.output_pipeline',
        'services.screenshot.key_input',
        'services.uia_scroll_targets',
        'sounddevice',
        'lameenc',
        # Embedded sensor backend: pythonnet provides the CLR bridge used to load
        # LibreHardwareMonitorLib.dll in-process for CPU/RAM/GPU/SSD temperatures.
        'clr_loader',
        'pythonnet',
        # UI Automation for smart element/scroll capture. The UIAutomationClient
        # wrapper is generated in-memory at runtime (gen_dir=None when frozen),
        # so only comtypes itself needs to be bundled.
        'comtypes',
        'comtypes.client',
        'comtypes.client._generate',
        'comtypes.stream',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='TaskbarMonitor',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='src/assets/taskbar-monitor.ico',
)

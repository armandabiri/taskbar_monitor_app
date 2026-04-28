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
    ] + sounddevice_datas,
    hiddenimports=[
        'ui.cleanup_history_dialog',
        'ui.cleanup_result_dialog',
        'ui.snapshot_live_cleanup_dialog',
        'services.resource_control.history',
        'services.resource_control.snapshot_scope',
        'sounddevice',
        'lameenc',
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

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules

hiddenimports = ['boss_mqtt_pb2', 'dashboard', 'chat_handler', 'llm_client', 'mqtt_chat', 'mqtt_monitor', 'browser', 'monitor', 'job_hunter', 'models', 'config', 'flask', 'requests', 'websocket', 'boss_mqtt_pb2']
hiddenimports += collect_submodules('flask')
hiddenimports += collect_submodules('requests')
hiddenimports += collect_submodules('websocket')


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('config.py', '.'), ('.api_key', '.'), ('boss_mqtt_pb2.py', '.'), ('boss_mqtt.proto', '.')],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['tkinter', 'matplotlib', 'numpy', 'pandas', 'PIL', 'cv2'],
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
    name='boss-agent',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

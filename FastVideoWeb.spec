# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

root = Path.cwd()
datas = [
    ('web', 'web'),
    ('assets', 'assets'),
    ('tools', 'tools'),
]

# Demucs/Torch is intentionally kept as a sidecar venv in portable releases.
# It is not bundled into the PyInstaller executable because it is very large
# and more reliable when run by .venv-demucs\Scripts\python.exe.

a = Analysis(
    ['web_app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
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
    name='FastVideoWeb',
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
    icon=['assets\\app_icon.ico'],
)

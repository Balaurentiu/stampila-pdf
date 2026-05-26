# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [
    ('stampila_curata.png', '.'),
    ('stampila_curata_dark.png', '.'),
    ('stampila_extra.png', '.'),
    ('stampila_original.jpg', '.'),
]
datas += collect_data_files('webview')

hiddenimports = collect_submodules('webview') + [
    'PIL._tkinter_finder',
    'engineio.async_drivers.threading',
]

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='StampilaPDF',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon='stampila_curata.png',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='StampilaPDF',
)

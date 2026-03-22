# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules


project_root = Path.cwd()

datas = []
binaries = []
hiddenimports = []


def collect_steamcmd_bundle(root: Path):
    steamcmd_datas = []
    if not root.exists():
        return steamcmd_datas

    steamcmd_executable = root / "steamcmd.exe"
    if steamcmd_executable.exists():
        steamcmd_datas.append((str(steamcmd_executable), "steamcmd"))

    return steamcmd_datas

datas += collect_data_files("PySide6")
binaries += collect_dynamic_libs("PySide6")
hiddenimports += collect_submodules("PySide6")
hiddenimports += [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebChannel",
]

steamcmd_root = project_root / "steamcmd"
if steamcmd_root.exists():
    datas += collect_steamcmd_bundle(steamcmd_root)

a = Analysis(
    ["gui.py"],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    [],
    exclude_binaries=True,
    name="StellarisModManager",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="StellarisModManager",
)

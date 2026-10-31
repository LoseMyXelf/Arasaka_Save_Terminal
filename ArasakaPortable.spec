# Portable folder build. Distribute the complete folder including _internal.
from pathlib import Path

project = Path(SPECPATH)
assets = project / "assets"

a = Analysis(
    [str(project / "CyberpunkSaveEditor.py")],
    pathex=[str(project)],
    binaries=[],
    datas=[
        (str(assets / "arasaka_logo.png"), "assets"),
        (str(assets / "arasaka_logo.ico"), "assets"),
    ],
    hiddenimports=[],
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
    name="ArasakaSaveTerminal",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    icon=str(assets / "arasaka_logo.ico"),
)

portable = COLLECT(
    exe, a.binaries, a.datas,
    strip=False, upx=False, name="ArasakaSaveTerminal",
)

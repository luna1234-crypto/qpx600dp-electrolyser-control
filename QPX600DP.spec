"""Maintainable PyInstaller one-folder build for the Windows desktop application."""

from pathlib import Path


PROJECT_ROOT = Path(SPECPATH)
APPLICATION_NAME = "QPX600DP Electrolyser Control"


analysis = Analysis(
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=[
        (str(PROJECT_ROOT / "assets" / "qpx600dp_app_icon.png"), "assets"),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "serial",
        "serial.serialwin32",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(PROJECT_ROOT / "packaging" / "runtime_paths.py")],
    excludes=[
        "pytest",
        "_pytest",
        "tests",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)

# Qt 6.10 uses the Windows ICU runtime.  Do not bundle an unrelated ICU copy
# that happens to be present on a developer machine's PATH (for example,
# Poppler's version-suffixed ICU build), because Windows may load it ahead of
# the compatible system runtime and QtCore then fails with an entry-point error.
_foreign_icu_names = {"icudt78.dll", "icuuc.dll"}
analysis.binaries = [
    item for item in analysis.binaries if Path(item[0]).name.lower() not in _foreign_icu_names
]

python_archive = PYZ(analysis.pure)

executable = EXE(
    python_archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name=APPLICATION_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "qpx600dp_app_icon.ico"),
)

release = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=APPLICATION_NAME,
)

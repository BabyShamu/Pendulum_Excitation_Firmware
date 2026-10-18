from pathlib import Path

desktop_dir = Path(SPECPATH)
package_dir = desktop_dir / "src" / "pendulum_workbench"
assets_dir = package_dir / "assets"

analysis = Analysis(
    [str(package_dir / "__main__.py")],
    pathex=[str(desktop_dir / "src")],
    binaries=[],
    datas=[(str(assets_dir), "pendulum_workbench/assets")],
    hiddenimports=["serial", "serial.tools.list_ports_windows"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="Pendulum Workbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(assets_dir / "pendulum_icon.ico"),
)
collection = COLLECT(
    executable,
    analysis.binaries,
    analysis.datas,
    strip=False,
    upx=False,
    name="Pendulum Workbench",
)
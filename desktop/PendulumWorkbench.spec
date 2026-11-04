from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

try:
    import matlab.engine  # noqa: F401  (pure-Python part only; native lib stays in MATLAB)
except Exception as exc:  # pragma: no cover
    raise SystemExit(
        "MATLAB Engine for Python is not importable in the build environment: "
        + str(exc) + "\nRun desktop\\install_matlab_engine.py first."
    )
matlab_modules = collect_submodules("matlab")

desktop_dir = Path(SPECPATH)
package_dir = desktop_dir / "src" / "pendulum_workbench"
assets_dir = package_dir / "assets"

analysis = Analysis(
    [str(package_dir / "__main__.py")],
    pathex=[str(desktop_dir / "src")],
    binaries=[],
    datas=[(str(assets_dir), "pendulum_workbench/assets")],
    hiddenimports=["serial", "serial.tools.list_ports_windows", "pendulum_workbench.experiments.shared_matlab_runner"] + matlab_modules,
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
    analysis.binaries,
    analysis.datas,
    [],
    name="PendulumWorkbench",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(assets_dir / "pendulum_icon.ico"),
)

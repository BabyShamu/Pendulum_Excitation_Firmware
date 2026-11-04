"""Run a Simscape experiment in a shared, already-open MATLAB session.

Invoked as a subprocess by the PySide6 Workbench to avoid freezing its UI.
The user must run matlab.engine.shareEngine in the open MATLAB session.

The pure-Python ``matlab`` package is bundled in the standalone EXE. Its native
library (matlabengineforpython_abi3.pyd) and the MATLAB runtime DLLs are NOT
bundled; they are loaded from the installed MATLAB that owns the shared session.

Exit codes:
  0 success
  2 usage error
  3 MATLAB Engine for Python not bundled / not installed
  4 no shared MATLAB session available
  5 MATLAB simulation execution error
  6 MATLAB Engine incompatible with the Python runtime / MATLAB installation
  7 failed to connect to the shared MATLAB session
"""
from __future__ import annotations

import ctypes
import io
import os
import re
import sys
from pathlib import Path

EXIT_USAGE = 2
EXIT_NOT_BUNDLED = 3
EXIT_NO_SESSION = 4
EXIT_SIMULATION = 5
EXIT_INCOMPATIBLE = 6
EXIT_CONNECT = 7

ENV_MATLAB_ROOT = "PENDULUM_MATLAB_ROOT"
_ENGINE_PYD = "matlabengineforpython_abi3.pyd"


def _quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _engine_dir(root: Path) -> Path:
    return root / "extern" / "engines" / "python" / "dist" / "matlab" / "engine" / "win64"


def _root_from_exe(exe: str) -> Path | None:
    path = Path(exe)
    if path.name.lower() != "matlab.exe" or len(path.parents) < 3:
        return None
    root = path.parents[2]
    return root if (_engine_dir(root) / _ENGINE_PYD).is_file() else None


def _running_matlab_exes() -> dict[int, str]:
    """Return {pid: image path} for running MATLAB.exe processes (Windows)."""
    if sys.platform != "win32":
        return {}
    from ctypes import wintypes

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.OpenProcess.restype = wintypes.HANDLE
    result: dict[int, str] = {}
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot in (None, wintypes.HANDLE(-1).value):
        return result
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(ProcessEntry)
        more = kernel32.Process32FirstW(wintypes.HANDLE(snapshot), ctypes.byref(entry))
        while more:
            if entry.szExeFile.lower() == "matlab.exe":
                handle = kernel32.OpenProcess(0x1000, False, entry.th32ProcessID)
                if handle:
                    size = wintypes.DWORD(1024)
                    buffer = ctypes.create_unicode_buffer(1024)
                    if kernel32.QueryFullProcessImageNameW(
                        wintypes.HANDLE(handle), 0, buffer, ctypes.byref(size)
                    ):
                        result[int(entry.th32ProcessID)] = buffer.value
                    kernel32.CloseHandle(wintypes.HANDLE(handle))
            more = kernel32.Process32NextW(wintypes.HANDLE(snapshot), ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(wintypes.HANDLE(snapshot))
    return result


def _release_key(root: Path) -> str:
    return root.name


def find_matlab_roots() -> list[Path]:
    """Candidate MATLAB roots: env override, running instances, then installs."""
    roots: list[Path] = []

    def add(root: Path | None) -> None:
        if root is not None and root not in roots:
            roots.append(root)

    override = os.environ.get(ENV_MATLAB_ROOT)
    if override:
        add(Path(override))
    running = [_root_from_exe(exe) for exe in _running_matlab_exes().values()]
    for root in sorted((r for r in running if r), key=_release_key, reverse=True):
        add(root)
    base = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "MATLAB"
    if base.is_dir():
        for child in sorted(base.iterdir(), key=_release_key, reverse=True):
            if (_engine_dir(child) / _ENGINE_PYD).is_file():
                add(child)
    return roots


def _prepare_native_engine(root: Path) -> None:
    bin_dir = root / "bin" / "win64"
    extern_dir = root / "extern" / "bin" / "win64"
    os.environ["PATH"] = (str(extern_dir) + os.pathsep + str(bin_dir) + os.pathsep
                          + os.environ.get("PATH", ""))
    if hasattr(os, "add_dll_directory"):
        os.add_dll_directory(str(bin_dir))
        os.add_dll_directory(str(extern_dir))
    sys.path.insert(0, str(extern_dir))
    sys.path.insert(0, str(_engine_dir(root)))


def _session_pid(name: str) -> int | None:
    match = re.fullmatch(r"MATLAB_(\d+)", name)
    return int(match.group(1)) if match else None


def _import_engine():
    """Import matlab.engine. Returns (module, None, None) or (None, code, message)."""
    import importlib.util

    try:
        bundled = importlib.util.find_spec("matlab") is not None
    except (ImportError, ValueError):
        bundled = False
    if not bundled:
        return None, EXIT_NOT_BUNDLED, (
            "MATLAB Engine not bundled: the 'matlab' Python package is missing from "
            "this Workbench build. Rebuild PendulumWorkbench.exe "
            "after running desktop\\install_matlab_engine.py."
        )
    roots = find_matlab_roots()
    if not roots:
        return None, EXIT_INCOMPATIBLE, (
            "MATLAB Engine incompatible: no MATLAB installation containing "
            + _ENGINE_PYD + " was found. Install MATLAB (R2022b or newer "
            "for this Python runtime) or set " + ENV_MATLAB_ROOT + " to the MATLAB root."
        )
    _prepare_native_engine(roots[0])
    try:
        import matlab.engine as engine
    except Exception as exc:
        return None, EXIT_INCOMPATIBLE, (
            "MATLAB Engine incompatible with the Python runtime ("
            + sys.version.split()[0] + ") or MATLAB at " + str(roots[0])
            + ". MATLAB Engine for Python supports Python 3.9-3.13 and must come "
            "from the same MATLAB release.\nDetails: " + str(exc)
        )
    return engine, roots[0], None


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("Usage: shared_matlab_runner <thesis_root> <experiment_folder>", flush=True)
        return EXIT_USAGE
    thesis = Path(args[0]).resolve()
    folder = Path(args[1]).resolve()

    print("Python runtime: " + sys.version.split()[0]
          + (" (frozen)" if getattr(sys, "frozen", False) else ""), flush=True)
    engine_module, root_or_code, message = _import_engine()
    if engine_module is None:
        print(message, flush=True)
        return int(root_or_code)
    root = root_or_code
    print("MATLAB Engine loaded from: " + str(root), flush=True)

    try:
        sessions = engine_module.find_matlab()
    except Exception as exc:
        print("MATLAB Engine incompatible: could not enumerate shared sessions: "
              + str(exc), flush=True)
        return EXIT_INCOMPATIBLE
    if not sessions:
        print(
            "No shared MATLAB session available. In the already-open MATLAB "
            "Command Window, run: matlab.engine.shareEngine",
            flush=True,
        )
        return EXIT_NO_SESSION

    exes = _running_matlab_exes()
    session = sessions[0]
    for name in sessions:
        pid = _session_pid(name)
        owner = _root_from_exe(exes[pid]) if pid in exes else None
        if owner == root:
            session = name
            break
    print("Connecting to shared MATLAB session: " + session
          + " (available: " + ", ".join(sessions) + ")", flush=True)
    try:
        matlab_engine = engine_module.connect_matlab(session)
    except Exception as exc:
        print("Failed to connect to shared MATLAB session " + session + ": "
              + str(exc) + "\nThe session may have closed or belong to a different "
              "MATLAB release than " + str(root) + ".", file=sys.stderr, flush=True)
        return EXIT_CONNECT

    output = io.StringIO()
    errors = io.StringIO()
    commands = (
        f"project=openProject({_quote(thesis)});"
        "cd(project.RootFolder);"
        f"runExperimentSimulation({_quote(folder)});"
        f"if exist(fullfile({_quote(folder)},'simulation_input.json'),'file');"
        f"manifest=jsondecode(fileread(fullfile({_quote(folder)},'manifest.json')));"
        "if contains(string(manifest.experiment.experiment_mode),'AUTO_RESONANCE') || "
        "strcmp(string(manifest.experiment.experiment_mode),'PHASE_TRACKED_SINE_AUTO_RESONANCE');"
        f"compareExperiment({_quote(folder)},Plot=false,SaveResults=true);"
        "end;end;"
    )
    try:
        matlab_engine.eval(commands, nargout=0, stdout=output, stderr=errors)
    except Exception as exc:
        print(output.getvalue(), flush=True)
        print("MATLAB simulation execution error: " + str(exc), file=sys.stderr, flush=True)
        return EXIT_SIMULATION
    print(output.getvalue(), flush=True)
    if errors.getvalue().strip():
        print(errors.getvalue(), file=sys.stderr, flush=True)
    print("Shared MATLAB simulation completed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

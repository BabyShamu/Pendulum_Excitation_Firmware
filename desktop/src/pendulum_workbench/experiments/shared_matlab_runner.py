"""Run a Simscape experiment in a shared, already-open MATLAB session.

Invoked as a subprocess by the PySide6 Workbench to avoid freezing its UI.
The user must run matlab.engine.shareEngine in the open MATLAB session.
The matching MATLAB Engine for Python package must be installed into the
same Workbench virtual environment.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path


def _quote(value: Path | str) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2:
        print("Usage: shared_matlab_runner <thesis_root> <experiment_folder>", flush=True)
        return 2
    thesis = Path(args[0]).resolve()
    folder = Path(args[1]).resolve()
    try:
        import matlab.engine
    except ImportError:
        print(
            "MATLAB Engine for Python is not installed in this virtual environment. "
            "Install the version matching your MATLAB installation; then run "
            "matlab.engine.shareEngine in the open MATLAB Command Window.",
            flush=True,
        )
        return 3
    sessions = matlab.engine.find_matlab()
    if not sessions:
        print(
            "No shared MATLAB session found. In the already-open MATLAB "
            "Command Window, run: matlab.engine.shareEngine",
            flush=True,
        )
        return 4
    print("Connecting to shared MATLAB session: " + sessions[0], flush=True)
    try:
        engine = matlab.engine.connect_matlab(sessions[0])
        output = io.StringIO()
        errors = io.StringIO()
        commands = (
            f"project=openProject({_quote(thesis)});"
            "cd(project.RootFolder);"
            f"runExperimentSimulation({_quote(folder)});"
            f"if exist(fullfile({_quote(folder)},'simulation_input.json'),'file');"
            f"manifest=jsondecode(fileread(fullfile({_quote(folder)},'manifest.json')));"
            "if strcmp(string(manifest.experiment.experiment_mode),'AUTO_RESONANCE_PARAMETRIC');"
            f"compareExperiment({_quote(folder)},Plot=false,SaveResults=true);"
            "end;end;"
        )
        engine.eval(commands, nargout=0, stdout=output, stderr=errors)
        print(output.getvalue(), flush=True)
        if errors.getvalue().strip():
            print(errors.getvalue(), file=sys.stderr, flush=True)
        print("Shared MATLAB simulation completed.", flush=True)
        return 0
    except Exception as exc:
        print("Shared MATLAB error: " + str(exc), file=sys.stderr, flush=True)
        return 5


if __name__ == "__main__":
    raise SystemExit(main())

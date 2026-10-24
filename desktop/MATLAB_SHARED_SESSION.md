# Run Simscape using an already-open MATLAB session

The Experiment Library now offers **Use Open MATLAB** or **Start New MATLAB**
after the experiment is selected (and after optional saved-cache prompt).

## First-time setup

The existing MATLAB application is reused through the official
MATLAB Engine API for Python. This is optional; the previous separate
`matlab.exe -batch` path remains available.

1. Activate the Workbench `.venv` in its `desktop` directory.
2. Install a **MATLAB Engine for Python version compatible with your
   installed MATLAB release and Python version**, following MathWorks'
   MATLAB Engine instructions. For recent supported releases, the
   package may be installed with:
   `python -m pip install matlabengine`.
   Consult the release compatibility table if this fails.
3. In the already-running MATLAB desktop, open the `Pendulum_Thesis`
   project, then run:
   ```matlab
   matlab.engine.shareEngine
   ```
4. In the Workbench, choose **Experiment Library**, select a run,
   **Run / Reuse Simscape** -> **Use Open MATLAB**.

The Workbench starts only a small Python process to attach to the
shared engine; it **does not launch another MATLAB application**.
The desktop remains responsive. The existing
`runExperimentSimulation.m` cache logic still determines whether an
actual Simscape run is needed. MATLAB's current project and model
remain subject to the same validation as before.

## Limitations and troubleshooting

- A MATLAB session already open but *not shared* cannot be connected.
  If prompted that no shared session is available, run
  `matlab.engine.shareEngine` inside MATLAB.
- If the Workbench virtual environment has no `matlab.engine`, install
  the release-compatible package into that environment.
- Connection to a shared session may still take time, and a first model
  compilation may take substantial time. This change primarily avoids
  repeated fresh MATLAB startup and project loading.
- The shared session is the user's live MATLAB desktop; tasks will run
  there and may affect its workspace/current directory.
- Diagnostics are written to each experiment's
  `derived/matlab_run.log`.
- A saved simulation can still be opened instantly without MATLAB.

## Target columns and plotting

The Experiment Library displays Target angle, Target achieved, and
Target time alongside the existing experiment fields. Time is computed
from the first valid recorded telemetry sample to the first observed
absolute-angle threshold crossing, with linear interpolation. It is
**not necessarily time since actuator onset or latch release**.
A missing target or unusable recording appears as an em dash.

The simulation and comparison image titles show mode and applicable
parameters (amplitude, frequency, feedback phase, initial angle).
Open-loop sine has no feedback phase; Free Swing has no excitation
amplitude/frequency by design.

This feature has not yet been run on the user's Windows/MATLAB
installation. Verify against a previously recorded completed run
before relying on newly generated performance metrics.

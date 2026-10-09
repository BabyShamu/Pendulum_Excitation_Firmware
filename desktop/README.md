# Pendulum Workbench Desktop App

The desktop package provides a PySide6 dashboard with explicit mock, real STM32, and saved-experiment modes. Milestone 4 adds experiment recording and reopening. Motion commands and Simscape integration remain unavailable.

## Run

```powershell
python -m pip install -e .
python -m pendulum_workbench
```

Run those commands from this directory. The dashboard starts in clearly labelled mock mode. To monitor hardware, select **Real STM32**, refresh and select a serial port, then connect. The app enables firmware live telemetry and periodically requests read-only status; it does not send motion commands.

Each real connection writes received serial lines to `%LOCALAPPDATA%/PendulumWorkbench/serial-captures/<session-id>/serial_capture.log`. The capture path is shown in the dashboard; malformed and partial lines remain in the raw log.

## Experiment Records

Use **Start Recording** and **Stop Recording** in Experiment Controls to save the current mock or connected STM32 telemetry. Each run receives a unique ID under `%LOCALAPPDATA%/PendulumWorkbench/experiments/<experiment-id>/` and contains:

- `manifest.json`: timestamps, completion/interruption status, notes, source and version provenance, and configuration. Physical and excitation values not supplied for the run remain `null` with `TBD` status.
- `serial_capture.log`: authoritative JSONL raw serial lines, including exact received payload bytes and timing. Mock runs have an empty raw serial log.
- `telemetry.csv`: normalized telemetry with schema version 2 and per-sample receive timestamps.
- `derived/`: reserved for future processed outputs; this milestone does not generate analysis or simulation artifacts.

Completed and interrupted records can be reopened through **File > Open Experiment**. A manifest still marked `RECORDING` when reopened is reconciled to `INTERRUPTED`.

## Hardware Startup Observation

On 2026-10-09, the app connected to `COM6` but received no telemetry until the NUCLEO reset button was pressed. This is an observed, unresolved startup/reconnection behavior, not the expected connection workflow. Investigate why telemetry does not begin after a normal connection and determine whether a reset should ever be required; do not treat manual reset as the normal operating procedure.

## Test

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```
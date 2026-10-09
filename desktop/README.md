# Pendulum Workbench Desktop App

The desktop package currently provides a PySide6 shell, application state model, service contracts, and mock telemetry. It does not connect to the STM32, control hardware, record experiments, or integrate Simscape.

## Run

```powershell
python -m pip install -e .
python -m pendulum_workbench
```

Run those commands from this directory. The dashboard starts with a clearly labelled mock stream; the device connection remains disconnected.

## Test

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```
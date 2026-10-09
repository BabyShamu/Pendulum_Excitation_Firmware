# Pendulum Workbench Desktop App

The desktop package provides a PySide6 dashboard with explicit mock and real-STM32 modes, read-only hardware status, and raw serial capture. It does not expose motion controls, record experiment metadata, or integrate Simscape.

## Run

```powershell
python -m pip install -e .
python -m pendulum_workbench
```

Run those commands from this directory. The dashboard starts in clearly labelled mock mode. To monitor hardware, select **Real STM32**, refresh and select a serial port, then connect. The app enables firmware live telemetry and periodically requests read-only status; it does not send motion commands.

Each real connection writes received serial lines to `%LOCALAPPDATA%/PendulumWorkbench/serial-captures/<session-id>/serial_capture.log`. The capture path is shown in the dashboard; malformed and partial lines remain in the raw log.

## Test

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```
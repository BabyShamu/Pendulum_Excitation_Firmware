# Pendulum Workbench Desktop App

The desktop package provides a PySide6 workbench with Monitor, Experiment, and Maintenance workspaces. The normal operator startup is REAL STM32; Mock telemetry remains available programmatically for tests and development. Saved experiments open from the Experiment workspace. Experiment excitation commands and Simscape integration remain unavailable.

## Run

```powershell
python -m pip install -e .
python -m pendulum_workbench
```

Run those commands from this directory. The app opens in Monitor. Refresh ports, select a COM port, and connect. The desktop starts continuous telemetry and requests compact operational status on connection/reconnection and after relevant maintenance operations; there is no recurring full-status poll.

## Communication Categories

- **Telemetry:** continuous device-indexed samples using `sample_index`, MCU `time_s`, `angle_deg`, and `position_mm`. Device time is the scientific time axis. The desktop stores receive UTC, receive elapsed time, and source RX line index as provenance.
- **Status:** compact operational state, requested at synchronization points rather than continuously.
- **Events:** meaningful connection, limit, homing, fault, maintenance, manual-motion, and recording transitions shown in Monitor.
- **Diagnostics:** low-level firmware diagnostics use the explicit `diag` command and the desktop diagnostic logger; RX byte dumps are compiled out unless `FIRMWARE_DEBUG_RX` is enabled.

## AS5600 Startup and Recovery

Firmware performs up to three startup angle-read attempts, separated by 25 ms. After three consecutive runtime read failures it tries bounded I2C bus recovery (up to nine released SCL pulses, peripheral reset/reinit, then AS5600 address verification). Further recovery attempts are limited to one per second while failures continue. These are communication-recovery parameters, not physical or safety limits.

The existing firmware behavior that calls `SetAngleZero()` at boot is preserved. I2C recovery does not call `SetAngleZero()` and does not change the stored raw zero reference. A successful sensor recovery requires a fresh BIT to restore readiness.

## Manual Pivot Commissioning

Maintenance provides press-and-hold UP/DOWN using the existing 150 steps/s jog rate. The desktop sends typed, correlated start/keepalive/stop commands every 100 ms. Firmware stops motion if no matching keepalive arrives within `MANUAL_MOTION_DEADMAN_TIMEOUT_MS`, whose current development default is 350 ms and can be overridden with a PlatformIO build flag. Neither interval is a validated safety rating; verify both with the actual stage, cable, switches, and host/serial latency before release. Firmware limit inputs remain authoritative.

Each real connection writes received serial lines to `%LOCALAPPDATA%/PendulumWorkbench/serial-captures/<session-id>/serial_capture.log`. The capture path is shown in the dashboard; malformed and partial lines remain in the raw log.

## Pendulum Latch Commissioning

Maintenance provides correlated `latch_hold`, `latch_open`, and `latch_status` commands. The displayed state is the firmware's commanded state only; there is no physical position feedback, and the latch is not part of the System Check/BIT.

The hobby servo signal uses D9 / PC7 with TIM8 channel 2 PWM at 50 Hz. The current manually calibrated pulse values are `SERVO_LATCH_HOLD_PULSE_US = 700 us` and `SERVO_LATCH_OPEN_PULSE_US = 1700 us`; these are pulse widths, not servo angles. Firmware starts with PC7 inactive and latch state UNKNOWN; it does not actuate the servo at boot. The latch state is command-only because there is no physical position sensor.

Homing requires the Pendulum Latch to be explicitly commanded OPEN in the current STM32 session. Startup remains manual: the firmware does not automatically OPEN the latch during boot, connection, or a Home request. Confirm OPEN in Maintenance before starting Home.

Servo power is temporarily connected to 5 V for commissioning only. This is not the validated power design; plan for a suitable external regulated supply with a common ground. Calibrate mechanically in Maintenance by commanding HOLD and OPEN separately; do not sweep positions or treat the servo as setting the pendulum's initial angle.

## Experiment Records

Use **Start Recording** and **Stop Recording** in Experiment Controls to save the current mock or connected STM32 telemetry. Each run receives a unique ID under `%LOCALAPPDATA%/PendulumWorkbench/experiments/<experiment-id>/` and contains:

- `manifest.json`: timestamps, completion/interruption status, notes, source and version provenance, and configuration. Physical and excitation values not supplied for the run remain `null` with `TBD` status.
- `serial_capture.log`: authoritative JSONL raw serial lines, including exact received payload bytes and timing. Mock runs have an empty raw serial log.
- `telemetry.csv`: schema version 3 with primary columns `sample_index,time_s,angle_deg,position_mm`, followed by receive/RX provenance and quality. Earlier schema layouts remain loadable.
- `derived/`: reserved for future processed outputs; this milestone does not generate analysis or simulation artifacts.

Completed and interrupted records can be reopened through **Experiment > Open Experiment** or **File > Open Experiment**. A manifest still marked `RECORDING` when reopened is reconciled to `INTERRUPTED`.

## Hardware Startup Observation

On 2026-10-09, the app connected to `COM6` but received no telemetry until the NUCLEO reset button was pressed. This is an observed, unresolved startup/reconnection behavior, not the expected connection workflow. Investigate why telemetry does not begin after a normal connection and determine whether a reset should ever be required; do not treat manual reset as the normal operating procedure.

## Test

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```
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

## Building Windows EXE

Build on Windows with 64-bit Python installed and available as `python` on PATH.
The package requires Python >=3.10; Python 3.13 is recommended as the version used
for local desktop testing. An internet connection is required on the build PC
to install dependencies. PyInstaller is a build-only extra, not a runtime dependency.

From this directory in CMD, run (or double-click the batch file):

```bat
build_exe.bat
```

The script creates/reuses `desktop/.venv-build`, installs the editable package and
the pinned PyInstaller build extra, removes only `desktop/build` and `desktop/dist`,
and builds the committed spec. It stops on errors and leaves the result visible.
Close a previously running packaged app before rebuilding.

Equivalent commands from this directory in CMD are:

```bat
python -m venv .venv-build
.venv-build\Scripts\python.exe -m pip install -e ".[build]"
.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm --workpath build --distpath dist PendulumWorkbench.spec
```

The primary supported build is **ONEDIR**:
`desktop/dist/Pendulum Workbench/Pendulum Workbench.exe`.
Distribute the entire `Pendulum Workbench` folder, including `_internal`.
Target PCs do not need Python installed. PyInstaller's PySide6 hooks bundle the
Qt libraries/plugins required by the imported Qt modules, including the Windows
platform plugin. The spec includes the application assets and Windows pyserial
port-enumeration backend. Resource lookup is relative to the bundled module,
not the current working directory.

COM access still requires Windows to recognize the NUCLEO/ST-Link serial device
and its driver. Packaging does not install that driver. Windows SmartScreen may
warn about the unsigned EXE; unsigned local builds are expected until code
signing is added. Build on a trusted machine and distribute trusted artifacts.

**Optional ONEFILE** is secondary and not the canonical spec. It extracts Qt at
startup and can launch more slowly. Build it with this CMD command from this directory:

```bat
.venv-build\Scripts\python.exe -m PyInstaller --clean --noconfirm --onefile --windowed --noupx --name "Pendulum Workbench Onefile" --specpath build --paths src --icon "src\pendulum_workbench\assets\pendulum_icon.ico" --add-data "src\pendulum_workbench\assets;pendulum_workbench/assets" --hidden-import serial.tools.list_ports_windows "src\pendulum_workbench\__main__.py"
```

Its EXE appears at `desktop/dist/Pendulum Workbench Onefile.exe`; the generated
secondary spec stays under ignored `desktop/build`. Do not replace the committed
ONEDIR spec with it.

### Manual Smoke Test

Use a disconnected device initially. Do not automatically drive the motor.

1. Double-click the built EXE.
2. Confirm no console window appears.
3. Confirm the application icon appears in the window/taskbar.
4. Confirm the window opens maximized.
5. Confirm the Experiment page loads.
6. Confirm COM ports can be refreshed/enumerated.
7. Confirm REAL STM32 mode is selected/available.
8. With hardware connected and motion idle, manually confirm connect/disconnect.
9. Manually run System Check; do not run Home or excitation as a packaging test.
10. Confirm experiment configuration UI loads.
11. Confirm the recording directory can be created/written in the normal user data location.
12. Confirm there are no missing Qt DLL/platform-plugin errors.
13. Confirm there are no missing icon/assets errors.

## Test

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -s tests -v
```
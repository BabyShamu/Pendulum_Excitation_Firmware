# Auto-Resonance Controller V2 (experimental)

This mode is **added to the existing firmware**, not a replacement for the
legacy parametric or phase-tracked-sine controllers.

## Architecture

- STM32 NUCLEO-G474RE reads the AS5600 pendulum angle every 5 ms.
- The V2 controller squares the angle, removes a running DC estimate,
  generates a quadrature signal with the firmware's 5-ms discrete filter,
  applies independent AGC, and derives the phase using atan2.
- A selectable harmonic waveform is calculated using the same coefficients
  used in the Thesis Simulink Fourier AR studies:
  - F1: sin(alpha)
  - F2: (2/sqrt(3)) * (sin(alpha) + sin(3*alpha)/6)
  - F3: 1.20713*sin(alpha) + 0.28101*sin(3*alpha) + 0.07389*sin(5*alpha)
- Neither pendulum length nor natural frequency is required.
- V2 uses its own controller state in `src/auto_resonance_v2.h`.
- The controller writes its carriage target into the existing Parametric V2
  trajectory owner (`g_parametricV2TargetSteps`). TIM6 then executes that
  target with the existing acceleration-controlled scheduler and runtime
  acceleration configuration. The existing timer ownership, fault handling,
  homing requirements, angle-sensor checks, and stage travel bounds are reused.
- Auto-Resonance V2 remains a separate controller mode: its 5-ms update and
  Fourier command generation are unchanged, while `g_parametricRunning` and
  `g_parametricV2Enabled` are used only to hand its target to the Parametric V2
  actuator path.

The PLL is **not** part of V2: simulation comparisons showed the original
quadrature phase reference outperforming the experimental smoothed PLL.

## Commissioning command (serial CLI)

Only after homing, explicitly opening the servo latch, verifying angle
feedback and interlocks, and placing the carriage near its homed center:

```text
ar_v2 start 0.5 22.5 1
ar_v2 stop
```

Arguments: amplitude in mm, phase offset in degrees, Fourier mode 1/2/3.
The current experimental firmware rejects amplitude above **2 mm**.
No V2 motion starts automatically at boot.

During V2 activity, the CLI rejects other motion commands. The global
`stop` command also stops V2. Existing closed-loop actuator faults stop
the V2 mode.

The initial start command is not a substitute for checking limit switches,
E-stop behavior, hardware clearances, actual motor polarity, commanded vs.
measured carriage displacement, and the ability to stop motion immediately.

## Output

The V2 CLI emits:
```text
time_s,angle_deg,command_mm,motor_mm,actual_mm,phase_rad,waveform
```

The desktop application **has not yet been updated to expose V2** as a
native Experiment workspace controller. Its current CSV stream is a
diagnostic firmware stream, not a new desktop experiment schema.

## Known limitations / validation required

1. The first-order DC estimate in V2 approximates, but does **not**
   reproduce, the original sixth-order Simulink DC blocker.
2. The AGC, filtering and phase conventions must be compared against the
   latest Simulink controller using recorded sensor data.
3. The compiled binary and its target-hardware behavior have **not** been
   validated by this change. Build with `pio run -e nucleo_g474re`.
4. The physical polarity constant `CLOSED_LOOP_COMMAND_SIGN` is reused
   from the existing firmware. Verify it before pumping experiments.
5. The existing driver caps step rate; reaching a target step count does
   not prove that actual carriage motion follows the Fourier harmonics.
   Measure tracking before increasing excitation amplitude.
6. The startup command currently requires the carriage within 0.5 mm of
   the homed midpoint, independent of the 2 mm amplitude cap.
7. Use guard/clearance and conservative commissioning, and verify
   hardware emergency-stop operation independently.

## Offline trajectory validation

The firmware build and desktop firmware tests include an event-driven offline
model of `ParametricV2OnTimer()`. It checks the configured 5-us STEP pulse,
10-us DIR setup interval, 150-Hz startup rate, 4-kHz rate cap, 18,000-Hz/s
acceleration setting, target reversals, immediate stop, limit faults, sensor
faults, and restart from the current commanded step position. The AR V2
F1/F2/F3 command path is also exercised with its 5-ms update and slew limit.

For 2-mm synthetic commands, the model produced the following commanded-step
tracking results over a 2-second run:

| Mode | Frequency | RMS error | Maximum error | Peak step rate |
|---|---:|---:|---:|---:|
| F1 | 0.5 Hz | 0.016 mm | 0.050 mm | 329 Hz |
| F1 | 1 Hz | 0.082 mm | 0.150 mm | 537 Hz |
| F1 | 2 Hz | 0.338 mm | 0.550 mm | 931 Hz |
| F2 | 1 Hz | 0.187 mm | 0.425 mm | 849 Hz |
| F3 | 1 Hz | 0.273 mm | 0.700 mm | 1,057 Hz |

At 5 Hz, the F1/F2/F3 synthetic-command RMS errors were 1.327/1.708/1.907
mm respectively, so those rates are not considered suitable for the current
commissioning stage. These are commanded step-count results, not measured
carriage displacement. The model omits interrupt latency, GPIO/driver timing,
missed steps, mechanical compliance, and sensor/serial timing; hardware
tracking remains unverified.

The controller is ready for the next software/bench validation stage, not for
unattended physical excitation. Verify polarity, limit response, actual
carriage tracking, and emergency-stop behavior with conservative hardware
tests before increasing scope.

## Planned validation stages

1. Build and static-check; review firmware safety integrations.
2. AS5600 readings and commanded-phase logging at stationary carriage.
3. Small-stroke motor-output and polarity tests, confirming stop/faults.
4. F1 closed-loop response; F2/F3 only after tracking validated.
5. Repeat with a changed pendulum length **without any controller change**.

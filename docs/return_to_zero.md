# Return to zero

The firmware uses `g_positionSteps == 0` as the homed carriage center. This
coordinate is independent of the AS5600 pendulum-angle zero set by `zero`.

Every normally stopped pivot experiment requests an asynchronous return to
zero. The return uses the Parametric V2 TIM6 trajectory scheduler, including
its configured acceleration and speed limits. The main loop remains available
for UART processing, telemetry, angle safety polling, limit monitoring, and
fault handling while the carriage moves.

The default TIM6 acceleration is `100000 steps/s^2`. It remains configurable
with `v2_accel`; the accepted runtime range is `6000..1000000 steps/s^2`.
Automatic return-to-zero uses the same current runtime setting and does not
silently select a separate acceleration.

The supported motion modes are sine, legacy parametric, Parametric V2,
time-limited V2 actuator tests, legacy Auto-Resonance, Auto-Resonance V2,
phase-tracked sine, and bounded parametric closed-loop excitation. Their
normal stop and completion paths request the same return mechanism. Protective
faults, emergency stops, limit trips, the pendulum reaching the 90-degree
target angle (`target_reached`), sensor faults beyond
the permitted recovery policy, and lost homing abort the return instead of
starting or continuing motion.

## Manual command

```text
go_zero
```

`go_zero` is accepted only while idle, homed, fault-free, and angle-safety
ready. It does not change homing calibration, AS5600 angle zero, or experiment
statistics. If the carriage is already at zero, it reports completion without
moving. The existing `zero` command continues to calibrate the pendulum angle
sensor.

## Telemetry

Return motion emits:

```text
event: type=return_to_zero_started reason=<reason> position_steps=N
event: type=return_to_zero_completed position_steps=0 duration_ms=N
event: type=return_to_zero_aborted reason=<reason> position_steps=N
```

Automatic return is not a new experiment and does not create a new sensor
session. A step-count position of zero confirms only the firmware's commanded
step count; it does not independently verify physical carriage position or
missed-step absence. Physical commissioning must validate travel, limits,
acceleration, and emergency-stop behavior.

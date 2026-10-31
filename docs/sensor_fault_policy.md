# AS5600 sensor fault policy

The firmware uses one bounded sensor session for each active experiment. The
session records failed reads, distinct outages, recovery attempts and results,
and outage durations. A session emits:

```text
event: type=sensor_session_start policy=complete_target
event: type=angle_sensor_unavailable ...
event: type=angle_sensor_recovery ...
event: type=angle_sensor_restored ...
event: type=sensor_session_summary failed_reads=N outages=N recovery_attempts=N recovery_successes=N recovery_failures=N total_outage_ms=N max_outage_ms=N last_outage_ms=N
```

Feedback controllers (parametric, Parametric V2/AR V2, Parametric Test, and
phase-tracked sine) do not generate a new target after a failed angle read.
Their existing target remains available to the trajectory actuator, so it may
complete within the established travel, limit, acceleration, and speed
envelope. Feedback resumes only after a validated angle read. Once the transient
250 ms grace period expires, these controllers enter `WAITING_FOR_SENSOR`,
emit `event: type=controller_waiting_for_sensor`, and remain active without
generating new feedback-dependent targets. TIM6 may complete only the last
validated target. Recovery attempts continue in the background; a successful
validated read emits `event: type=controller_resumed` and permits control to
resume without a target jump.

Open-loop motion (sine, fixed V2 actuator test, manual motion, and ordinary
open-loop stepping) may continue through a transient sensor outage while the
existing safety gate remains within its 250 ms bounded grace period. Recovery
continues automatically. A longer outage trips the existing sensor fault so pendulum-angle protection
is not silently removed. Without an AS5600 sample, the firmware cannot enforce
the 90-degree pendulum-angle limit; indefinite sensor-independent operation is
therefore not enabled for unconstrained open-loop motion. Upper/lower limits,
emergency stop, homing, travel, speed, and acceleration protections remain
authoritative during feedback-controller waiting.

The session summary is based on firmware commanded state and sensor
communication events. It does not measure physical carriage displacement and
cannot establish missed-step or pendulum dynamics behavior. Bench validation
must verify the outage grace period, recovery timing, angle-limit response,
emergency stop, and physical clearances.

AS5600 samples are also checked for plausibility before they are committed to
the validated angle state. A sample with an implausible circular step change is
reported as `event: type=angle_measurement_rejected` with its timestamp, raw
value, previous validated angle, and rejection reason. Rejected samples do not
update controller estimators, target-angle completion, telemetry angle values,
or experiment statistics; the normal telemetry stream emits `NaN` for that
sample. The session summary includes `angle_samples_rejected`,
`max_validated_angle_deg`, and `max_rejected_angle_deg`. The plausibility rule
is based on measurement continuity, not on the absolute angle threshold, so a
credible gradual angle-limit violation still triggers the existing immediate
protective trip.

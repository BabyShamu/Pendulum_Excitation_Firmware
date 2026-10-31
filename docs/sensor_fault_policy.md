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
envelope. Feedback resumes only after a validated angle read.

Open-loop motion (sine, fixed V2 actuator test, manual motion, and ordinary
open-loop stepping) may continue through a transient sensor outage while the
existing safety gate remains within its 250 ms bounded grace period. Recovery
continues automatically. A longer outage trips the existing sensor fault so
pendulum-angle protection is not silently removed. Upper/lower limits,
emergency stop, homing, and previously detected angle-limit faults remain
authoritative.

The session summary is based on firmware commanded state and sensor
communication events. It does not measure physical carriage displacement and
cannot establish missed-step or pendulum dynamics behavior. Bench validation
must verify the outage grace period, recovery timing, angle-limit response,
emergency stop, and physical clearances.

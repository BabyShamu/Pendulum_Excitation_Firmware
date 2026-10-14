"""Replay a recorded Auto-Resonance trace through the phase-tracked target."""

from __future__ import annotations

import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path

from pendulum_workbench.domain.phase_tracked_sine import (
    PHASE_TRACKED_MAX_PHASE_RATE_RAD_S,
    PHASE_TRACKED_MIN_VECTOR_MAGNITUDE,
    PHASE_TRACKED_OMEGA_FILTER_TAU_S,
    PhaseConfidence,
    PhaseTracker,
    PhaseTrackedSineTarget,
)

REPLAY_PERIOD_S = 0.005
DC_BLOCK_TIME_CONST_S = 2.0
AGC_STEP = 0.0005
AGC_MAX_LOG_GAIN = 6.9077553
AGC_Z_EPSILON = 1e-20
AGC_FILTER_COEFFICIENT = 0.9048
DEFAULT_TRACE = (
    Path(__file__).resolve().parents[4]
    / "recordings"
    / "stage4_closed_loop_inverted_growth_01.csv"
)


@dataclass(frozen=True, slots=True)
class TraceSample:
    time_s: float
    theta_deg: float
    autores_motor_target_mm: float
    actual_mm: float


def load_trace(path: Path) -> list[TraceSample]:
    """Read controller CSV rows, skipping PuTTY framing and other log text."""
    required = ("time_s", "theta_deg", "motor_target_mm", "actual_mm")
    rows: list[TraceSample] = []
    header: dict[str, int] | None = None

    with path.open("r", encoding="utf-8-sig", errors="replace", newline="") as source:
        for fields in csv.reader(source):
            if header is None:
                if all(name in fields for name in required):
                    header = {name: fields.index(name) for name in required}
                continue
            try:
                sample = TraceSample(
                    time_s=float(fields[header["time_s"]]),
                    theta_deg=float(fields[header["theta_deg"]]),
                    autores_motor_target_mm=float(fields[header["motor_target_mm"]]),
                    actual_mm=float(fields[header["actual_mm"]]),
                )
            except (IndexError, ValueError):
                continue
            if not all(math.isfinite(value) for value in (
                sample.time_s,
                sample.theta_deg,
                sample.autores_motor_target_mm,
                sample.actual_mm,
            )):
                continue
            if rows and sample.time_s <= rows[-1].time_s:
                continue
            rows.append(sample)

    if len(rows) < 3:
        raise ValueError(f"no usable controller trace found in {path}")
    return rows


def _agc(input_value: float, log_gain: float) -> tuple[float, float]:
    output = input_value * math.exp(log_gain)
    z = max(input_value * input_value * math.exp(2.0 * log_gain), AGC_Z_EPSILON)
    updated_gain = min(AGC_MAX_LOG_GAIN, log_gain - AGC_STEP * math.log(z))
    return output, updated_gain


def _resample_theta(rows: list[TraceSample], time_s: float) -> float:
    right = 1
    while right < len(rows) and rows[right].time_s < time_s:
        right += 1
    if right >= len(rows):
        return rows[-1].theta_deg
    left = right - 1
    before = rows[left]
    after = rows[right]
    fraction = (time_s - before.time_s) / (after.time_s - before.time_s)
    return before.theta_deg + fraction * (after.theta_deg - before.theta_deg)


def _direction_reversals(values: list[float]) -> int:
    previous_direction = 0
    reversals = 0
    for before, after in zip(values, values[1:]):
        delta = after - before
        direction = 1 if delta > 1e-6 else -1 if delta < -1e-6 else 0
        if direction and previous_direction and direction != previous_direction:
            reversals += 1
        if direction:
            previous_direction = direction
    return reversals


def replay_trace(
    path: Path,
    *,
    amplitude_mm: float = 7.5,
    phase_offset_deg: float = 0.0,
) -> dict[str, float | int | str]:
    if not math.isfinite(amplitude_mm) or not 0.0 < amplitude_mm <= 7.5:
        raise ValueError("replay amplitude must be greater than zero and no more than 7.5 mm")
    if not math.isfinite(phase_offset_deg):
        raise ValueError("phase offset must be finite")

    rows = load_trace(path)
    tracker = PhaseTracker(
        min_vector_magnitude=PHASE_TRACKED_MIN_VECTOR_MAGNITUDE,
        max_phase_rate_rad_s=PHASE_TRACKED_MAX_PHASE_RATE_RAD_S,
        omega_filter_tau_s=PHASE_TRACKED_OMEGA_FILTER_TAU_S,
    )

    first_time = rows[0].time_s
    last_time = rows[-1].time_s
    current_time = first_time
    dc_estimate: float | None = None
    previous_dc_blocked = 0.0
    quadrature_filtered = 0.0
    direct_log_gain = 0.0
    quadrature_log_gain = 0.0
    target_generator: PhaseTrackedSineTarget | None = None
    phase_targets: dict[float, float] = {}
    omega_values: list[float] = []
    max_phase_step = 0.0
    previous_phase: float | None = None
    propagated_samples = 0
    measured_samples = 0
    fault_time_s: float | None = None
    sample_count = 0

    while current_time <= last_time + 1e-9:
        theta_rad = math.radians(_resample_theta(rows, current_time))
        theta_squared = theta_rad * theta_rad
        if dc_estimate is None:
            dc_estimate = theta_squared
            dc_blocked = 0.0
        else:
            alpha = REPLAY_PERIOD_S / (REPLAY_PERIOD_S + DC_BLOCK_TIME_CONST_S)
            dc_estimate += alpha * (theta_squared - dc_estimate)
            dc_blocked = theta_squared - dc_estimate
            quadrature_filtered = (
                AGC_FILTER_COEFFICIENT * quadrature_filtered
                + 20.0 * dc_blocked
                - 20.0 * previous_dc_blocked
            )
            previous_dc_blocked = dc_blocked

        direct_agc, direct_log_gain = _agc(dc_blocked, direct_log_gain)
        quadrature_agc, quadrature_log_gain = _agc(
            quadrature_filtered,
            quadrature_log_gain,
        )
        phase = tracker.update(current_time, direct_agc, quadrature_agc)
        sample_count += 1
        if phase.status is PhaseConfidence.MEASURED:
            measured_samples += 1
        elif phase.status is PhaseConfidence.PROPAGATED:
            propagated_samples += 1
        if phase.status is PhaseConfidence.FAULT and fault_time_s is None:
            fault_time_s = current_time
            sample_count += 1
            break

        if phase.phase_tracked_rad is not None:
            if previous_phase is not None:
                max_phase_step = max(
                    max_phase_step,
                    abs(phase.phase_tracked_rad - previous_phase),
                )
            previous_phase = phase.phase_tracked_rad
        if phase.omega_2x_rad_s is not None:
            omega_values.append(phase.omega_2x_rad_s)

        if phase.phase_tracked_rad is not None and target_generator is None:
            target_generator = PhaseTrackedSineTarget(
                amplitude_mm=amplitude_mm,
                phase_offset_deg=phase_offset_deg,
                start_position_mm=rows[0].actual_mm,
                start_time_s=current_time,
            )
        if target_generator is None:
            target_mm = rows[0].actual_mm
        else:
            _, target_mm = target_generator.target(current_time, phase.phase_tracked_rad)
        motor_target_mm = min(7.5, max(-7.5, -target_mm))
        phase_targets[round(current_time, 6)] = motor_target_mm

        sample_count += 0
        current_time = first_time + sample_count * REPLAY_PERIOD_S

    compared: list[tuple[float, float]] = []
    for row in rows:
        replay_time = round(
            first_time + round((row.time_s - first_time) / REPLAY_PERIOD_S) * REPLAY_PERIOD_S,
            6,
        )
        if replay_time in phase_targets:
            compared.append((row.autores_motor_target_mm, phase_targets[replay_time]))
    if not compared:
        raise ValueError("trace did not yield phase-tracked target samples")

    deltas = [phase_target - autores_target for autores_target, phase_target in compared]
    omega_deltas = [after - before for before, after in zip(omega_values, omega_values[1:])]
    return {
        "trace": str(path),
        "rows": len(rows),
        "duration_s": last_time - first_time,
        "replay_samples": sample_count,
        "compared_samples": len(compared),
        "amplitude_mm": amplitude_mm,
        "phase_offset_deg": phase_offset_deg,
        "autores_motor_target_reversals": _direction_reversals([item[0] for item in compared]),
        "phase_motor_target_reversals": _direction_reversals([item[1] for item in compared]),
        "max_abs_target_delta_mm": max(abs(value) for value in deltas),
        "rms_target_delta_mm": math.sqrt(sum(value * value for value in deltas) / len(deltas)),
        "autores_motor_target_min_mm": min(item[0] for item in compared),
        "autores_motor_target_max_mm": max(item[0] for item in compared),
        "phase_motor_target_min_mm": min(item[1] for item in compared),
        "phase_motor_target_max_mm": max(item[1] for item in compared),
        "measured_phase_samples": measured_samples,
        "propagated_phase_samples": propagated_samples,
        "phase_fault_time_s": fault_time_s if fault_time_s is not None else "none",
        "max_abs_phase_step_rad": max_phase_step,
        "max_abs_phase_step_rate_rad_s": max_phase_step / REPLAY_PERIOD_S,
        "omega_2x_min_rad_s": min(omega_values) if omega_values else "none",
        "omega_2x_max_rad_s": max(omega_values) if omega_values else "none",
        "max_abs_omega_step_rad_s": max((abs(value) for value in omega_deltas), default=0.0),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare recorded Auto-Resonance targets with a phase-tracked sine replay."
    )
    parser.add_argument("trace", nargs="?", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--amplitude-mm", type=float, default=7.5)
    parser.add_argument("--phase-deg", type=float, default=0.0)
    args = parser.parse_args()
    report = replay_trace(
        args.trace,
        amplitude_mm=args.amplitude_mm,
        phase_offset_deg=args.phase_deg,
    )
    for key, value in report.items():
        print(f"{key}: {value}")
    print("replay_note: input is decimated 20 ms telemetry, interpolated onto the firmware 5 ms update period")


if __name__ == "__main__":
    main()
"""Replay a recorded Auto-Resonance trace through the phase-tracked target."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path

from pendulum_workbench.domain.phase_tracked_sine import (
    PHASE_TRACKED_MAX_PHASE_RATE_RAD_S,
    PHASE_TRACKED_ACQUISITION_SAMPLES,
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


@dataclass(frozen=True, slots=True)
class ReplayPoint:
    time_s: float
    theta_deg: float
    actual_mm: float
    direct_phase_signal: float
    quadrature_phase_signal: float
    phase_magnitude: float
    phase_raw_rad: float | None
    phase_unwrapped_rad: float | None
    phase_tracked_rad: float | None
    omega_2x_rad_s: float | None
    phase_status: str
    normalized_sine: float
    phase_target_mm: float
    phase_motor_target_mm: float
    existing_autores_target_mm: float


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


def _resample(rows: list[TraceSample], time_s: float, field: str) -> float:
    right = 1
    while right < len(rows) and rows[right].time_s < time_s:
        right += 1
    if right >= len(rows):
        return float(getattr(rows[-1], field))
    left = right - 1
    before = rows[left]
    after = rows[right]
    fraction = (time_s - before.time_s) / (after.time_s - before.time_s)
    before_value = float(getattr(before, field))
    after_value = float(getattr(after, field))
    return before_value + fraction * (after_value - before_value)


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


def _smoothness(values: list[float]) -> dict[str, float | int]:
    deltas = [after - before for before, after in zip(values, values[1:])]
    return {
        "max_abs_sample_delta_mm": max((abs(value) for value in deltas), default=0.0),
        "rms_sample_delta_mm": (
            math.sqrt(sum(value * value for value in deltas) / len(deltas))
            if deltas
            else 0.0
        ),
        "direction_reversals": _direction_reversals(values),
        "target_min_mm": min(values),
        "target_max_mm": max(values),
    }


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return ordered[index]


def _dominant_frequency(times: list[float], values: list[float]) -> float | None:
    if len(times) < 8 or len(values) != len(times):
        return None
    stride = max(1, math.ceil(len(times) / 4096))
    times = times[::stride]
    values = values[::stride]
    duration = times[-1] - times[0]
    sample_period = duration / (len(times) - 1)
    max_frequency = min(10.0, 0.45 / sample_period)
    coarse_step = max(0.01, 1.0 / duration)
    mean = sum(values) / len(values)
    windowed = [
        (value - mean) * (0.5 - 0.5 * math.cos(2.0 * math.pi * index / (len(values) - 1)))
        for index, value in enumerate(values)
    ]

    def power(frequency: float) -> float:
        real = math.fsum(
            value * math.cos(2.0 * math.pi * frequency * (time - times[0]))
            for time, value in zip(times, windowed)
        )
        imag = math.fsum(
            value * math.sin(2.0 * math.pi * frequency * (time - times[0]))
            for time, value in zip(times, windowed)
        )
        return real * real + imag * imag

    coarse = [0.1 + index * coarse_step for index in range(int((max_frequency - 0.1) / coarse_step) + 1)]
    peak = max(coarse, key=power)
    fine_step = coarse_step / 20.0
    fine = [
        max(0.1, peak - coarse_step) + index * fine_step
        for index in range(round(2.0 * coarse_step / fine_step) + 1)
    ]
    return max((frequency for frequency in fine if frequency <= max_frequency), key=power)


def _write_replay_csv(path: Path, points: list[ReplayPoint], phase_offset_deg: float) -> None:
    columns = (
        "time_s",
        "theta_deg",
        "actual_mm",
        "direct_phase_signal",
        "quadrature_phase_signal",
        "phase_magnitude",
        "phase_raw_rad",
        "phase_unwrapped_rad",
        "phase_tracked_rad",
        "omega_2x_rad_s",
        "phase_status",
        "phase_offset_deg",
        "normalized_sine",
        "phase_target_mm",
        "phase_motor_target_mm",
        "existing_autores_target_mm",
    )
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for point in points:
            row = {name: getattr(point, name) for name in columns if hasattr(point, name)}
            row["time_s"] = point.time_s
            row["phase_offset_deg"] = phase_offset_deg
            writer.writerow(row)


def replay_trace(
    path: Path,
    *,
    amplitude_mm: float = 7.5,
    phase_offset_deg: float = 0.0,
    output_path: Path | None = None,
) -> dict[str, object]:
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
    target_generator: PhaseTrackedSineTarget | None = None
    points: list[ReplayPoint] = []
    magnitudes: list[float] = []
    omega_values: list[float] = []
    max_phase_step = 0.0
    previous_phase: float | None = None
    propagated_samples = 0
    measured_samples = 0
    fault_time_s: float | None = None
    acquisition_time_s: float | None = None
    confidence_transitions: dict[str, int] = {}
    confidence_loss_episodes = 0
    previous_status: PhaseConfidence | None = None
    sample_count = 0

    while current_time <= last_time + 1e-9:
        theta_deg = _resample(rows, current_time, "theta_deg")
        theta_rad = math.radians(theta_deg)
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

        phase_magnitude = math.hypot(dc_blocked, quadrature_filtered)
        phase = tracker.update(current_time, dc_blocked, quadrature_filtered)
        if previous_status is not None and phase.status != previous_status:
            transition = f"{previous_status.value}->{phase.status.value}"
            confidence_transitions[transition] = confidence_transitions.get(transition, 0) + 1
        if (
            phase.status is PhaseConfidence.PROPAGATED
            and previous_status is not PhaseConfidence.PROPAGATED
        ):
            confidence_loss_episodes += 1
        previous_status = phase.status
        if phase.status is PhaseConfidence.MEASURED:
            measured_samples += 1
        elif phase.status is PhaseConfidence.PROPAGATED:
            propagated_samples += 1
        magnitudes.append(phase_magnitude)
        if phase.locked and acquisition_time_s is None:
            acquisition_time_s = current_time - first_time

        if phase.phase_tracked_rad is not None:
            if previous_phase is not None:
                max_phase_step = max(
                    max_phase_step,
                    abs(phase.phase_tracked_rad - previous_phase),
                )
            previous_phase = phase.phase_tracked_rad
        if phase.omega_2x_rad_s is not None:
            omega_values.append(phase.omega_2x_rad_s)

        if phase.locked and target_generator is None:
            target_generator = PhaseTrackedSineTarget(
                amplitude_mm=amplitude_mm,
                phase_offset_deg=phase_offset_deg,
                start_position_mm=-rows[0].actual_mm,
                start_time_s=current_time,
            )
        if target_generator is None:
            normalized_sine = 0.0
            target_mm = -rows[0].actual_mm
        else:
            normalized_sine, target_mm = target_generator.target(
                current_time,
                phase.phase_tracked_rad,
            )
        motor_target_mm = min(7.5, max(-7.5, -target_mm))
        existing_target_mm = _resample(rows, current_time, "autores_motor_target_mm")
        points.append(
            ReplayPoint(
                time_s=current_time - first_time,
                theta_deg=theta_deg,
                actual_mm=_resample(rows, current_time, "actual_mm"),
                direct_phase_signal=dc_blocked,
                quadrature_phase_signal=quadrature_filtered,
                phase_magnitude=phase_magnitude,
                phase_raw_rad=phase.phase_raw_rad,
                phase_unwrapped_rad=tracker.measured_phase_unwrapped_rad,
                phase_tracked_rad=phase.phase_tracked_rad,
                omega_2x_rad_s=phase.omega_2x_rad_s,
                phase_status=phase.status.value,
                normalized_sine=normalized_sine,
                phase_target_mm=target_mm,
                phase_motor_target_mm=motor_target_mm,
                existing_autores_target_mm=existing_target_mm,
            )
        )
        if phase.status is PhaseConfidence.FAULT:
            fault_time_s = current_time - first_time
            sample_count += 1
            break

        sample_count += 1
        current_time = first_time + sample_count * REPLAY_PERIOD_S

    compared_indices = [
        round((row.time_s - first_time) / REPLAY_PERIOD_S)
        for row in rows
        if round((row.time_s - first_time) / REPLAY_PERIOD_S) < len(points)
    ]
    if not compared_indices:
        raise ValueError("trace did not yield phase-tracked target samples")

    existing_targets = [points[index].existing_autores_target_mm for index in compared_indices]
    phase_targets = [points[index].phase_motor_target_mm for index in compared_indices]
    controller_differences = [phase - existing for phase, existing in zip(phase_targets, existing_targets)]
    omega_deltas = [after - before for before, after in zip(omega_values, omega_values[1:])]
    replay_times = [point.time_s for point in points]
    angle_frequency = _dominant_frequency(
        [row.time_s - first_time for row in rows],
        [row.theta_deg for row in rows],
    )
    target_frequency = _dominant_frequency(
        replay_times,
        [point.phase_motor_target_mm for point in points],
    )
    report: dict[str, object] = {
        "trace": str(path),
        "rows": len(rows),
        "duration_s": last_time - first_time,
        "replay_samples": sample_count,
        "compared_samples": len(compared_indices),
        "amplitude_mm": amplitude_mm,
        "phase_offset_deg": phase_offset_deg,
        "existing_autores_smoothness": _smoothness(existing_targets),
        "phase_tracked_sine_smoothness": _smoothness(phase_targets),
        "max_abs_controller_difference_mm": max(abs(value) for value in controller_differences),
        "rms_controller_difference_mm": math.sqrt(
            sum(value * value for value in controller_differences) / len(controller_differences)
        ),
        "phase_magnitude_min": min(magnitudes),
        "phase_magnitude_q10": _percentile(magnitudes, 0.10),
        "phase_magnitude_q25": _percentile(magnitudes, 0.25),
        "phase_magnitude_median": _percentile(magnitudes, 0.50),
        "phase_magnitude_max": max(magnitudes),
        "phase_magnitude_threshold": PHASE_TRACKED_MIN_VECTOR_MAGNITUDE,
        "acquisition_samples_required": PHASE_TRACKED_ACQUISITION_SAMPLES,
        "measured_phase_samples": measured_samples,
        "propagated_phase_samples": propagated_samples,
        "confidence_transitions": confidence_transitions,
        "confidence_loss_episodes": confidence_loss_episodes,
        "phase_acquisition_time_s": acquisition_time_s if acquisition_time_s is not None else "none",
        "phase_fault_time_s": fault_time_s if fault_time_s is not None else "none",
        "max_abs_phase_step_rad": max_phase_step,
        "max_abs_phase_step_rate_rad_s": max_phase_step / REPLAY_PERIOD_S,
        "omega_2x_min_rad_s": min(omega_values) if omega_values else "none",
        "omega_2x_max_rad_s": max(omega_values) if omega_values else "none",
        "max_abs_omega_step_rad_s": max((abs(value) for value in omega_deltas), default=0.0),
        "dominant_pendulum_angle_frequency_hz": angle_frequency if angle_frequency is not None else "none",
        "dominant_phase_target_frequency_hz": target_frequency if target_frequency is not None else "none",
        "phase_target_to_angle_frequency_ratio": (
            target_frequency / angle_frequency
            if target_frequency is not None and angle_frequency not in (None, 0.0)
            else "none"
        ),
        "smoothness_comparison_sample_period_s": (
            rows[1].time_s - rows[0].time_s
        ),
        "replay_note": "20 ms recorded telemetry interpolated to 5 ms; smoothness metrics use the common recorded 20 ms sample grid",
    }
    if output_path is not None:
        _write_replay_csv(output_path, points, phase_offset_deg)
        report["replay_csv"] = str(output_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Compare recorded Auto-Resonance targets with a phase-tracked sine replay."
    )
    parser.add_argument("trace", nargs="?", type=Path, default=DEFAULT_TRACE)
    parser.add_argument("--amplitude-mm", type=float, default=7.5)
    parser.add_argument("--phase-deg", type=float, default=0.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output_path = args.output or args.trace.with_name(
        f"{args.trace.stem}_phase_tracked_replay.csv"
    )
    report = replay_trace(
        args.trace,
        amplitude_mm=args.amplitude_mm,
        phase_offset_deg=args.phase_deg,
        output_path=output_path,
    )
    for key, value in report.items():
        print(f"{key}: {json.dumps(value, sort_keys=True) if isinstance(value, dict) else value}")


if __name__ == "__main__":
    main()
import argparse
import csv
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from pendulum_workbench.domain.angle_prediction import AngleDropoutPredictor, AngleSource


@dataclass(frozen=True, slots=True)
class MeasuredAngleSample:
    time_s: float
    angle_deg: float | None


@dataclass(frozen=True, slots=True)
class DropoutWindow:
    start_s: float
    duration_ms: float


@dataclass(frozen=True, slots=True)
class HiddenSamplePrediction:
    time_s: float
    ground_truth_angle_deg: float
    predicted_angle_deg: float
    absolute_error_deg: float


@dataclass(frozen=True, slots=True)
class DropoutWindowEvaluation:
    window: DropoutWindow
    predictions: tuple[HiddenSamplePrediction, ...]


@dataclass(frozen=True, slots=True)
class DropoutSweepRow:
    dropout_duration_ms: float
    mean_abs_error_deg: float | None
    rmse_deg: float | None
    max_abs_error_deg: float | None
    number_of_windows: int


def load_telemetry_csv(path: Path | str) -> list[MeasuredAngleSample]:
    source = Path(path)
    samples: list[MeasuredAngleSample] = []
    with source.open("r", encoding="utf-8", newline="") as telemetry_file:
        reader = csv.DictReader(telemetry_file)
        if reader.fieldnames is None or "angle_deg" not in reader.fieldnames:
            raise ValueError("telemetry.csv must contain an angle_deg column")
        time_column = next(
            (name for name in ("time_s", "device_time_s", "time_sec") if name in reader.fieldnames),
            None,
        )
        if time_column is None:
            raise ValueError("telemetry.csv must contain time_s, device_time_s, or time_sec")
        for row_number, row in enumerate(reader, start=2):
            try:
                time_s = float(row[time_column])
            except (TypeError, ValueError) as error:
                raise ValueError(f"Invalid time at telemetry.csv row {row_number}") from error
            if not math.isfinite(time_s):
                raise ValueError(f"Non-finite time at telemetry.csv row {row_number}")
            raw_angle = (row.get("angle_deg") or "").strip()
            try:
                angle = float(raw_angle) if raw_angle else None
            except ValueError:
                angle = None
            if angle is not None and not math.isfinite(angle):
                angle = None
            if samples and time_s < samples[-1].time_s:
                raise ValueError("telemetry samples must be ordered by nondecreasing time")
            samples.append(MeasuredAngleSample(time_s, angle))
    return samples


def simulate_dropout_window(
    samples: Sequence[MeasuredAngleSample],
    window: DropoutWindow,
) -> DropoutWindowEvaluation:
    if not math.isfinite(window.start_s):
        raise ValueError("dropout start time must be finite")
    if not math.isfinite(window.duration_ms) or window.duration_ms <= 0.0:
        raise ValueError("dropout duration must be finite and greater than zero")
    if not samples:
        return DropoutWindowEvaluation(window, ())

    duration_s = window.duration_ms / 1000.0
    end_s = window.start_s + duration_s
    predictor = AngleDropoutPredictor(max_prediction_age_s=duration_s)
    predictions: list[HiddenSamplePrediction] = []
    previous_time: float | None = None
    for sample in samples:
        if previous_time is not None and sample.time_s < previous_time:
            raise ValueError("telemetry samples must be ordered by nondecreasing time")
        previous_time = sample.time_s
        hidden = window.start_s <= sample.time_s < end_s
        estimate = predictor.observe(
            sample.time_s,
            None if hidden else sample.angle_deg,
        )
        if (
            hidden
            and sample.angle_deg is not None
            and estimate.angle_source == AngleSource.PREDICTED
            and estimate.predicted_angle_deg is not None
        ):
            error_deg = (estimate.predicted_angle_deg - sample.angle_deg + 180.0) % 360.0 - 180.0
            predictions.append(
                HiddenSamplePrediction(
                    time_s=sample.time_s,
                    ground_truth_angle_deg=sample.angle_deg,
                    predicted_angle_deg=estimate.predicted_angle_deg,
                    absolute_error_deg=abs(error_deg),
                )
            )
    return DropoutWindowEvaluation(window, tuple(predictions))


def evaluate_dropout_sweep(
    samples: Sequence[MeasuredAngleSample],
    durations_ms: Sequence[float],
    *,
    windows_per_duration: int = 5,
) -> list[DropoutSweepRow]:
    if windows_per_duration < 1:
        raise ValueError("windows_per_duration must be at least one")
    rows: list[DropoutSweepRow] = []
    for duration_ms in durations_ms:
        if not math.isfinite(duration_ms) or duration_ms <= 0.0:
            raise ValueError("dropout durations must be finite and greater than zero")
        windows = choose_dropout_windows(samples, duration_ms, windows_per_duration)
        evaluations = [simulate_dropout_window(samples, window) for window in windows]
        errors = [
            prediction.absolute_error_deg
            for evaluation in evaluations
            for prediction in evaluation.predictions
        ]
        if errors:
            mean_abs = sum(errors) / len(errors)
            rmse = math.sqrt(sum(error * error for error in errors) / len(errors))
            max_abs = max(errors)
        else:
            mean_abs = rmse = max_abs = None
        rows.append(
            DropoutSweepRow(
                dropout_duration_ms=duration_ms,
                mean_abs_error_deg=mean_abs,
                rmse_deg=rmse,
                max_abs_error_deg=max_abs,
                number_of_windows=sum(bool(evaluation.predictions) for evaluation in evaluations),
            )
        )
    return rows


def choose_dropout_windows(
    samples: Sequence[MeasuredAngleSample],
    duration_ms: float,
    windows_per_duration: int,
) -> list[DropoutWindow]:
    duration_s = duration_ms / 1000.0
    segments = _valid_segments(samples)
    candidates: list[DropoutWindow] = []
    for segment in segments:
        for start_index in range(2, len(segment)):
            start_s = segment[start_index].time_s
            if start_s + duration_s > segment[-1].time_s:
                continue
            if any(start_s <= sample.time_s < start_s + duration_s for sample in segment):
                candidates.append(DropoutWindow(start_s, duration_ms))
    if len(candidates) <= windows_per_duration:
        return candidates
    if windows_per_duration == 1:
        return [candidates[len(candidates) // 2]]
    indices = {
        round(index * (len(candidates) - 1) / (windows_per_duration - 1))
        for index in range(windows_per_duration)
    }
    return [candidates[index] for index in sorted(indices)]


def write_sweep_csv(path: Path | str, rows: Sequence[DropoutSweepRow]) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="") as output_file:
        writer = csv.writer(output_file, lineterminator="\n")
        writer.writerow(
            (
                "dropout_duration_ms",
                "mean_abs_error_deg",
                "rmse_deg",
                "max_abs_error_deg",
                "number_of_windows",
            )
        )
        for row in rows:
            writer.writerow(
                (
                    row.dropout_duration_ms,
                    _csv_number(row.mean_abs_error_deg),
                    _csv_number(row.rmse_deg),
                    _csv_number(row.max_abs_error_deg),
                    row.number_of_windows,
                )
            )


def _valid_segments(samples: Sequence[MeasuredAngleSample]) -> list[list[MeasuredAngleSample]]:
    segments: list[list[MeasuredAngleSample]] = []
    current: list[MeasuredAngleSample] = []
    for sample in samples:
        if sample.angle_deg is None or not math.isfinite(sample.angle_deg):
            if current:
                segments.append(current)
                current = []
        else:
            current.append(sample)
    if current:
        segments.append(current)
    return segments


def _csv_number(value: float | None) -> float | str:
    return "" if value is None else value


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate first-order angle dropout prediction using synthetic telemetry masks."
    )
    parser.add_argument("telemetry_csv", type=Path)
    parser.add_argument("--durations-ms", type=float, nargs="+", required=True)
    parser.add_argument("--windows-per-duration", type=int, default=5)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args()

    rows = evaluate_dropout_sweep(
        load_telemetry_csv(arguments.telemetry_csv),
        arguments.durations_ms,
        windows_per_duration=arguments.windows_per_duration,
    )
    output = arguments.output or arguments.telemetry_csv.with_name("angle_dropout_sweep.csv")
    write_sweep_csv(output, rows)
    print(f"Wrote {len(rows)} duration rows to {output}")


if __name__ == "__main__":
    main()
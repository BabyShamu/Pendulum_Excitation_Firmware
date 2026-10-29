"""Read-only target attainment from experiment telemetry.

A target is reached at the first crossing of abs(angle) >= target. The time
is relative to the first valid telemetry timestamp. Never substitute the
experiment start timestamp or pretend a missing target is a failed attempt.
"""
import csv
import math
from pathlib import Path


def target_metrics(directory: Path, manifest: dict) -> dict:
    details = manifest.get("experiment") or {}
    raw_target = details.get("target_angle_deg") if isinstance(details, dict) else None
    try:
        target = float(raw_target)
        if not math.isfinite(target) or target <= 0:
            raise ValueError()
    except (TypeError, ValueError):
        return {"target_angle_deg": None, "target_achieved": None,
                "target_time_s": None}
    # New recordings persist a verified target event using exactly the same
    # monotonic reference as actual_elapsed_duration_s. Never recalculate
    # these from telemetry, including five seconds of post-stop observation.
    if details.get("target_time_source") == "workbench_monotonic_motor_start_ack":
        achieved = details.get("target_achieved")
        event_time = details.get("target_time_s")
        duration = details.get("actual_elapsed_duration_s")
        if not isinstance(achieved, bool):
            achieved = False
        if achieved:
            try:
                event_time = float(event_time)
                duration = float(duration)
                if not (math.isfinite(event_time) and math.isfinite(duration)
                        and 0 <= event_time <= duration + 0.001):
                    raise ValueError()
            except (TypeError, ValueError):
                return {"target_angle_deg": target, "target_achieved": None,
                        "target_time_s": None, "time_source": "invalid_recorded_event"}
        return {"target_angle_deg": target, "target_achieved": achieved,
                "target_time_s": event_time if achieved else None,
                "time_source": "workbench_monotonic_motor_start_ack"}
    path = Path(directory) / "telemetry.csv"
    baseline = None
    previous = None
    crossing = None
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    t = float(row["time_s"])
                    angle = abs(float(row["angle_deg"]))
                except (TypeError, ValueError, KeyError):
                    continue
                if not math.isfinite(t) or not math.isfinite(angle):
                    continue
                if baseline is None:
                    baseline = t
                if crossing is None and angle >= target:
                    if previous is not None and previous[1] < target and t > previous[0]:
                        fraction = (target - previous[1]) / (angle - previous[1])
                        crossing = previous[0] + fraction * (t - previous[0])
                    else:
                        crossing = t
                previous = (t, angle)
    except OSError:
        return {"target_angle_deg": target, "target_achieved": None,
                "target_time_s": None}
    if baseline is None:
        return {"target_angle_deg": target, "target_achieved": None,
                "target_time_s": None}
    return {"target_angle_deg": target, "target_achieved": crossing is not None,
            "target_time_s": (crossing - baseline) if crossing is not None else None,
            "time_source": "legacy_first_telemetry_sample_estimate"}

"""Validated maximum-angle and fixed success-threshold analysis."""
import csv
import math
from pathlib import Path


SUCCESS_ANGLE_DEG = 90.0


def target_metrics(directory: Path, manifest: dict) -> dict:
    """Return compatibility-shaped metrics using validated angle samples only.

    The historical function name is retained so older callers continue to
    work. Historical target-angle fields are intentionally ignored.
    """
    details = manifest.get("experiment") or {}
    maximum = details.get("maximum_angle_deg", manifest.get("maximum_angle_deg"))
    try:
        maximum = float(maximum)
        if not math.isfinite(maximum):
            raise ValueError
    except (TypeError, ValueError):
        maximum = None

    if maximum is None:
        path = Path(directory) / "telemetry.csv"
        try:
            with path.open(encoding="utf-8", newline="") as handle:
                for row in csv.DictReader(handle):
                    if row.get("quality") not in (None, "", "valid"):
                        continue
                    try:
                        angle = abs(float(row["angle_deg"]))
                    except (TypeError, ValueError, KeyError):
                        continue
                    if math.isfinite(angle):
                        maximum = angle if maximum is None else max(maximum, angle)
        except OSError:
            pass

    outcome = details.get("outcome")
    if outcome not in {"SUCCESS", "COMPLETED", "STOPPED", "FAILED", "INVALID"}:
        if maximum is None:
            outcome = "INVALID"
        elif maximum >= SUCCESS_ANGLE_DEG:
            outcome = "SUCCESS"
        else:
            outcome = "COMPLETED"

    time_to_success = details.get(
        "time_to_success_s", manifest.get("time_to_success_s")
    )
    try:
        time_to_success = float(time_to_success)
        if not math.isfinite(time_to_success) or time_to_success < 0.0:
            raise ValueError
    except (TypeError, ValueError):
        time_to_success = None
    if outcome != "SUCCESS":
        # The metric is defined only for successful target crossings.
        time_to_success = None

    return {
        "maximum_angle_deg": maximum,
        "max_angle_valid": maximum is not None,
        "outcome": outcome,
        "time_to_success_s": time_to_success,
        # Compatibility keys for callers not yet migrated.
        "target_angle_deg": SUCCESS_ANGLE_DEG,
        "target_achieved": maximum >= SUCCESS_ANGLE_DEG if maximum is not None else None,
        "target_time_s": time_to_success,
    }

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
    return {
        "maximum_angle_deg": maximum,
        "max_angle_valid": maximum is not None,
        "outcome": outcome,
        # Compatibility keys for callers not yet migrated.
        "target_angle_deg": None,
        "target_achieved": maximum >= SUCCESS_ANGLE_DEG if maximum is not None else None,
        "target_time_s": None,
    }

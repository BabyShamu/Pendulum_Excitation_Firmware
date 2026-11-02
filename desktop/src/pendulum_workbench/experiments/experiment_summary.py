"""Shared read-only experiment projection for the Library and Excel export.

The manifest and recording files remain authoritative; this module never writes
back to either source.
"""
from __future__ import annotations

import math
from pathlib import Path

from pendulum_workbench.experiments.simulation_cache import inspect_simulation
from pendulum_workbench.experiments.target_metrics import target_metrics


MODE_LABELS = {
    "FREE_SWING": "Free Swing",
    "VERTICAL_SINUSOIDAL_EXCITATION": "Sinusoidal",
    "AUTO_RESONANCE_PARAMETRIC": "Auto Resonance",
    "PHASE_TRACKED_SINE_AUTO_RESONANCE": "Phase Tracked",
}

SIMULATION_LABELS = {
    "ready": "Saved",
    "missing": "Not simulated",
    "stale": "Stale",
    "incomplete": "Incomplete",
}


def finite_number(value):
    """Return a finite float or None, never treating booleans as numbers."""
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def experiment_summary(directory: Path, manifest: dict) -> dict:
    """Build the shared experiment fields shown in the Library and workbook."""
    details = manifest.get("experiment") or {}
    if not isinstance(details, dict):
        details = {}
    metrics = target_metrics(directory, manifest)
    simulation = inspect_simulation(directory)
    duration = details.get("actual_elapsed_duration_s")
    if duration is None:
        duration = details.get("commanded_duration_s")
    if duration is None:
        duration = details.get("duration_s")

    mode = str(details.get("experiment_mode") or "Recording")
    return {
        "experiment_id": str(manifest.get("experiment_id") or ""),
        "created_at": str(manifest.get("created_at") or ""),
        "name": str(manifest.get("name") or ""),
        "mode": MODE_LABELS.get(mode, mode),
        "archived": bool(manifest.get("archived")),
        "completion": str(manifest.get("completion_status") or ""),
        "duration_s": finite_number(duration),
        "initial_angle_deg": finite_number(details.get("measured_initial_angle_deg")),
        "measured_peak_angle_deg": finite_number(manifest.get("peak_absolute_angle_deg")),
        "maximum_angle_deg": finite_number(metrics["maximum_angle_deg"]),
        "outcome": metrics["outcome"],
        "target_achieved": metrics["target_achieved"],
        "simulation_status": SIMULATION_LABELS[simulation.state],
        "simulation": simulation,
    }

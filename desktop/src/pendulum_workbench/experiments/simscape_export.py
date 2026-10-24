"""Versioned, unit-explicit inputs for MATLAB/Simscape replay of physical runs.

This file describes measured/commanded inputs. It does not claim that a
closed-loop excitation can be reproduced from a fixed-frequency sine.
"""
import json
import os
from pathlib import Path
from typing import Any


def write_simscape_input(directory: Path, manifest: dict[str, Any]) -> Path:
    experiment = manifest.get("experiment") or {}
    mode = experiment.get("experiment_mode")
    mode_names = {
        "FREE_SWING": "free_swing",
        "VERTICAL_SINUSOIDAL_EXCITATION": "sinusoidal",
        "AUTO_RESONANCE_PARAMETRIC": "auto_resonance",
        "PHASE_TRACKED_SINE_AUTO_RESONANCE": "phase_tracked_sine",
    }
    # Do not invent an initial angular velocity or excitation phase.
    payload = {
        "schema_version": 1,
        "experiment_id": manifest["experiment_id"],
        "control_mode": mode_names.get(mode),
        "source_experiment_mode": mode,
        "initial_angle_deg": experiment.get("measured_initial_angle_deg"),
        "initial_angular_velocity_rad_s": None,
        "excitation_amplitude_mm": experiment.get("commanded_amplitude_mm"),
        "excitation_frequency_hz": experiment.get("commanded_frequency_hz"),
        "excitation_phase_rad": None,
        "commanded_phase_deg": experiment.get("commanded_phase_deg"),
        "requested_duration_s": experiment.get("requested_duration_s"),
        "actual_duration_s": experiment.get("actual_elapsed_duration_s"),
        "target_angle_deg": experiment.get("target_angle_deg"),
        "completion_status": manifest.get("completion_status"),
        "completion_reason": experiment.get("completion_reason"),
        "telemetry_file": manifest.get("files", {}).get("normalized_telemetry", "telemetry.csv"),
        "telemetry_columns": {
            "time": "time_s",
            "angle": "angle_deg",
            "carriage_position": "position_mm",
        },
        "replay_notes": (
            "Use telemetry.csv for recorded pendulum and carriage trajectories. "
            "For feedback modes, commanded amplitude/phase alone do not define "
            "the realized carriage motion. Unknown conditions are null."
        ),
    }
    destination = directory / "simulation_input.json"
    temporary = directory / ".simulation_input.json.tmp"
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(payload, output, indent=2, sort_keys=True, allow_nan=False)
        output.write("\n")
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, destination)
    return destination

"""Inspect MATLAB/Simscape simulation artifacts without running MATLAB."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path


_REQUIRED_FILES = (
    "simulation.mat",
    "simulation.csv",
    "pendulum_angle.png",
    "pivot_position.png",
    "simulation_overview.fig",
)


@dataclass(frozen=True, slots=True)
class SimulationStatus:
    state: str  # missing | ready | stale | incomplete
    message: str
    folder: Path
    metadata: dict


def inspect_simulation(experiment_directory: Path) -> SimulationStatus:
    folder = Path(experiment_directory) / "simulation"
    metadata_path = folder / "metadata.json"
    if not metadata_path.is_file():
        return SimulationStatus("missing", "No simulation saved", folder, {})
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        if not isinstance(metadata, dict):
            raise ValueError("Metadata is not a JSON object")
        if metadata.get("schema_version") != 1 or metadata.get("status") != "complete":
            return SimulationStatus("incomplete", "Incomplete simulation metadata", folder, metadata)
        manifest = json.loads((Path(experiment_directory) / "manifest.json").read_text(encoding="utf-8"))
        if metadata.get("experiment_id") != manifest.get("experiment_id"):
            return SimulationStatus("stale", "Simulation belongs to a different experiment", folder, metadata)
        fingerprint = metadata.get("fingerprint")
        if not isinstance(fingerprint, dict) or not fingerprint.get("input_sha256"):
            return SimulationStatus("incomplete", "Missing parameter fingerprint", folder, metadata)
        simulation_input = Path(experiment_directory) / "simulation_input.json"
        if not simulation_input.is_file():
            return SimulationStatus("stale", "Simulation input missing", folder, metadata)
        input_hash = hashlib.sha256(simulation_input.read_bytes()).hexdigest()
        if fingerprint["input_sha256"].lower() != input_hash:
            return SimulationStatus("stale", "Experiment simulation inputs changed", folder, metadata)
        missing = [name for name in _REQUIRED_FILES if not (folder / name).is_file()]
        if missing:
            return SimulationStatus("incomplete", "Missing simulation files: " + ", ".join(missing), folder, metadata)
        # Only MATLAB can validate model/dictionary/source hashes; the GUI
        # calls this 'saved' rather than 'guaranteed current'.
        return SimulationStatus("ready", "Saved simulation available (model freshness checked by MATLAB)", folder, metadata)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return SimulationStatus("incomplete", f"Cannot read simulation metadata: {error}", folder, {})

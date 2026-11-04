"""Mtime-invalidated cache for per-experiment statistics.

The Experiment Manager recomputes summaries, simulation status, and the
simulated peak angle on every search keystroke. Those values only change when
the underlying files change, so they are cached here and invalidated by file
modification time. The cache never outlives a change to the manifest,
telemetry, or simulation artifacts, and entries disappear with their folder.
"""
from __future__ import annotations

import math
from pathlib import Path

from pendulum_workbench.experiments.experiment_summary import (
    SIMULATION_LABELS,
    experiment_summary,
)
from pendulum_workbench.experiments.simulation_cache import (
    SimulationStatus,
    inspect_simulation,
)


def _mtime_ns(path: Path) -> int | None:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return None


def _manifest_fingerprint(manifest: dict) -> tuple:
    """Cheap content fingerprint of the fields that feed the summary.

    Guards the identity fast path against in-place manifest edits without
    serializing the whole dict on every call.
    """
    details = manifest.get("experiment")
    details = details if isinstance(details, dict) else {}
    return (
        manifest.get("experiment_id"),
        manifest.get("name"),
        manifest.get("created_at"),
        manifest.get("completion_status"),
        manifest.get("archived"),
        manifest.get("peak_absolute_angle_deg"),
        manifest.get("sample_count"),
        details.get("experiment_mode"),
        details.get("measured_initial_angle_deg"),
        details.get("actual_elapsed_duration_s"),
        details.get("commanded_duration_s"),
        details.get("duration_s"),
        details.get("maximum_angle_deg"),
        details.get("outcome"),
        details.get("time_to_success_s"),
    )


def _simulation_fingerprint(root: Path) -> tuple | None:
    """Fingerprint the simulation folder by its file set and newest mtime.

    Including the sorted file names ensures that deleting a required artifact
    invalidates the cache even when the folder's own mtime does not change.
    """
    try:
        entries = sorted(root.iterdir(), key=lambda entry: entry.name)
    except OSError:
        return None
    if not entries:
        return None
    names = tuple(entry.name for entry in entries)
    newest = 0
    for entry in entries:
        stamp = _mtime_ns(entry)
        if stamp is not None and stamp > newest:
            newest = stamp
    return (names, newest)


def simulated_max_angle(simulation_status: SimulationStatus) -> float | None:
    """Read the absolute maximum simulated angle from a valid saved CSV."""
    if simulation_status.state != "ready":
        return None
    import csv

    csv_path = simulation_status.folder / "simulation.csv"
    maximum = None
    try:
        with csv_path.open("r", encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            if "pendulum_angle_deg" not in (reader.fieldnames or []):
                return None
            for row in reader:
                try:
                    angle = float(row["pendulum_angle_deg"])
                except (ValueError, TypeError, KeyError):
                    continue
                if math.isfinite(angle):
                    magnitude = abs(angle)
                    if maximum is None or magnitude > maximum:
                        maximum = magnitude
    except (OSError, UnicodeError, csv.Error):
        return None
    return maximum


class ExperimentStatsCache:
    """Per-experiment cached statistics keyed by experiment directory."""

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}

    def clear(self) -> None:
        self._entries.clear()

    def discard(self, directory: Path) -> None:
        self._entries.pop(str(directory), None)

    def summary(self, directory: Path, manifest: dict) -> dict:
        """Return the shared experiment summary, recomputed only when stale.

        The fast path keys on the manifest object's identity plus a cheap
        content fingerprint, so repeated filtering with unchanged loaded
        manifests performs no disk I/O. Editing the manifest in place, or
        changing the manifest/telemetry on disk, invalidates the entry.

        The simulation fields are refreshed from the simulation cache on every
        call: creating or modifying simulation artifacts does not change the
        manifest or telemetry, so they must not be frozen inside the summary.
        """
        key = str(directory)
        fingerprint = _manifest_fingerprint(manifest)
        entry = self._entries.get(key)
        if (
            entry is not None
            and entry.get("manifest_obj") is manifest
            and entry.get("manifest_fingerprint") == fingerprint
            and "summary" in entry
        ):
            return self._with_current_simulation(Path(directory), manifest, entry)
        manifest_stamp = _mtime_ns(Path(directory) / "manifest.json")
        telemetry_stamp = _mtime_ns(Path(directory) / "telemetry.csv")
        if (
            entry is not None
            and entry.get("manifest_mtime") == manifest_stamp
            and entry.get("telemetry_mtime") == telemetry_stamp
            and "summary" in entry
        ):
            entry["manifest_obj"] = manifest
            entry["manifest_fingerprint"] = fingerprint
            # The manifest was just revalidated against disk; force the
            # simulation fingerprint to be re-read so new artifacts appear.
            entry.pop("simulation_fresh_for", None)
            return self._with_current_simulation(Path(directory), manifest, entry)
        summary = experiment_summary(Path(directory), manifest)
        entry = {
            "manifest_obj": manifest,
            "manifest_fingerprint": fingerprint,
            "manifest_mtime": manifest_stamp,
            "telemetry_mtime": telemetry_stamp,
            "summary": summary,
        }
        self._entries[key] = entry
        return self._with_current_simulation(Path(directory), manifest, entry)

    def _with_current_simulation(
        self, directory: Path, manifest: dict, entry: dict
    ) -> dict:
        """Overlay the current simulation status onto the cached summary."""
        summary = entry["summary"]
        simulation_status, _ = self.simulation(directory, manifest)
        if simulation_status is summary.get("simulation"):
            return summary
        updated = dict(summary)
        updated["simulation"] = simulation_status
        updated["simulation_status"] = SIMULATION_LABELS[simulation_status.state]
        entry["summary"] = updated
        return updated

    def simulation(
        self, directory: Path, manifest: dict | None = None
    ) -> tuple[SimulationStatus, float | None]:
        """Return (simulation status, simulated max angle), cached by mtime.

        The fast path reuses the cached result only when the simulation folder
        fingerprint was captured after the manifest was last validated. When
        the manifest is revalidated (a fresh load), the simulation fingerprint
        is re-read so newly created or modified artifacts are detected.
        """
        key = str(directory)
        entry = self._entries.get(key)
        if (
            manifest is not None
            and entry is not None
            and entry.get("manifest_obj") is manifest
            and entry.get("simulation_fresh_for") is manifest
            and "simulation" in entry
        ):
            return entry["simulation"]
        folder = Path(directory) / "simulation"
        fingerprint = _simulation_fingerprint(folder)
        if (
            entry is not None
            and entry.get("simulation_fingerprint") == fingerprint
            and "simulation" in entry
        ):
            if manifest is not None:
                entry["simulation_fresh_for"] = manifest
            return entry["simulation"]
        status = inspect_simulation(Path(directory))
        value = (status, simulated_max_angle(status))
        if entry is None:
            entry = {}
            self._entries[key] = entry
        entry["simulation_fingerprint"] = fingerprint
        entry["simulation"] = value
        if manifest is not None:
            entry["simulation_fresh_for"] = manifest
        return value

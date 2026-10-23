import csv
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

from pendulum_workbench.domain.models import DataSourceMode, TelemetrySample
from pendulum_workbench.experiments.simscape_export import write_simscape_input

TELEMETRY_CORE_COLUMNS = ("sample_index", "time_s", "angle_deg", "position_mm")
TELEMETRY_PROVENANCE_COLUMNS = (
    "source_rx_line_index",
    "received_elapsed_s",
    "received_at_utc",
    "quality",
)
TELEMETRY_COLUMNS = TELEMETRY_CORE_COLUMNS + TELEMETRY_PROVENANCE_COLUMNS
LEGACY_TELEMETRY_CORE_COLUMNS = (
    "sample_index",
    "source_rx_line_index",
    "device_time_s",
    "received_elapsed_s",
    "position_mm",
    "angle_deg",
    "quality",
)
LEGACY_TELEMETRY_COLUMNS = LEGACY_TELEMETRY_CORE_COLUMNS + ("received_at_utc",)
TELEMETRY_SCHEMA_VERSION = 3


@dataclass(frozen=True, slots=True)
class ExperimentSession:
    experiment_id: str
    directory: Path
    manifest: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LoadedExperiment:
    experiment_id: str
    directory: Path
    manifest: dict[str, Any]
    telemetry: tuple[TelemetrySample, ...]


class ExperimentRepository:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or self.default_root()

    @staticmethod
    def default_root() -> Path:
        app_data = os.environ.get("LOCALAPPDATA")
        if app_data:
            return Path(app_data) / "PendulumWorkbench" / "experiments"
        return Path.home() / ".local" / "share" / "pendulum-workbench" / "experiments"

    def create(
        self,
        *,
        name: str,
        notes: str,
        data_source: DataSourceMode,
        serial_port: str | None,
        created_at: datetime | None = None,
        experiment_id: str | None = None,
        experiment_metadata: dict[str, Any] | None = None,
    ) -> ExperimentSession:
        if not isinstance(data_source, DataSourceMode):
            raise TypeError(
                "ExperimentRepository.create data_source must be DataSourceMode, "
                f"got {type(data_source).__name__}"
            )
        experiment_id = experiment_id or str(uuid4())
        created_at = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)
        display_created_at = created_at.astimezone()
        details = experiment_metadata or {}
        mode = str(details.get("experiment_mode") or "Recording")
        mode = {
            "VERTICAL_SINUSOIDAL_EXCITATION": "Sinusoidal",
            "AUTO_RESONANCE_PARAMETRIC": "AutoResonance",
            "PHASE_TRACKED_SINE_AUTO_RESONANCE": "PhaseTracked",
        }.get(mode, mode)
        local_created_at = created_at.astimezone()
        mode_short = {
            "Sinusoidal": "VS",
            "AutoResonance": "AR",
            "PhaseTracked": "PT",
        }.get(mode, mode)
        tokens = [f"{local_created_at:%Y-%m-%d_%H-%M-%S}", mode_short]
        amplitude = details.get("commanded_amplitude_mm")
        frequency = details.get("commanded_frequency_hz")
        phase = details.get("commanded_phase_deg")
        if isinstance(amplitude, (int, float)):
            tokens.append(f"A{amplitude:g}mm")
        if isinstance(frequency, (int, float)):
            tokens.append(f"F{frequency:g}Hz")
        if isinstance(phase, (int, float)):
            tokens.append(f"P{phase:g}deg")
        label = "_".join(tokens)
        label = re.sub(r"[^A-Za-z0-9._-]", "_", label)
        directory = self.root / label
        suffix = 2
        while directory.exists():
            directory = self.root / f"{label}_{suffix}"
            suffix += 1
        directory.mkdir(parents=True, exist_ok=False)
        (directory / "derived").mkdir()
        (directory / "serial_capture.log").open("x", encoding="utf-8").close()
        with (directory / "telemetry.csv").open(
            "x", encoding="utf-8", newline=""
        ) as telemetry_file:
            csv.writer(telemetry_file, lineterminator="\n").writerow(TELEMETRY_COLUMNS)
        created_at = (created_at or datetime.now(timezone.utc)).astimezone(timezone.utc)

        try:
            application_version = version("pendulum-workbench")
            application_version_status = "package metadata"
        except PackageNotFoundError:
            application_version = "unknown"
            application_version_status = "package metadata unavailable"

        mode = data_source.value
        manifest: dict[str, Any] = {
            "schema_version": 1,
            "telemetry_schema_version": TELEMETRY_SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "name": name.strip() or experiment_id,
            "created_at": display_created_at.isoformat(),
            "started_at": display_created_at.isoformat(),
            "ended_at": None,
            "completion_status": "RECORDING",
            "interruption_reason": None,
            "notes": notes.strip(),
            "data_source": {
                "mode": mode,
                "serial_port": serial_port if data_source == DataSourceMode.REAL else None,
            },
            "application_version": application_version,
            "application_version_status": application_version_status,
            "application_commit_sha": None,
            "firmware_version": None,
            "firmware_commit_sha": None,
            "firmware_version_status": "not reported by current firmware; flashed build unknown",
            "telemetry_profile": "stm32-live-v1" if data_source == DataSourceMode.REAL else "mock-v1",
            "time_origin": {
                "time_s": "MCU/device time when available; mock source elapsed time for mock telemetry",
                "received_elapsed_s": "desktop monotonic elapsed time since serial connection",
                "received_at_utc": "desktop receive timestamp for each normalized sample",
            },
            "configuration": {
                "physical_parameters": {
                    "pendulum_length_m": {
                        "value": None,
                        "status": "TBD",
                        "source": None,
                    },
                    "bob_mass_kg": {"value": None, "status": "TBD", "source": None},
                    "damping_coefficient": {"value": None, "status": "TBD", "source": None},
                },
                "excitation": {
                    "type": {"value": None, "status": "TBD", "source": None},
                    "amplitude_mm": {"value": None, "status": "TBD", "source": None},
                    "frequency_hz": {"value": None, "status": "TBD", "source": None},
                },
            },
            "files": {
                "raw_serial_capture": "serial_capture.log",
                "normalized_telemetry": "telemetry.csv",
                "derived_data_directory": "derived/",
            },
            "sample_count": 0,
            "raw_line_count": 0,
        }
        if experiment_metadata is not None:
            manifest["experiment"] = dict(experiment_metadata)
        self.write_manifest(directory, manifest)
        return ExperimentSession(experiment_id, directory, manifest)

    def list_manifests(self) -> list[tuple[Path, dict[str, Any]]]:
        """List saved experiment metadata without loading large telemetry CSV files.

        Invalid or unreadable manifests are skipped so one damaged recording
        does not prevent the experiment browser from opening.
        """
        if not self.root.is_dir():
            return []
        records: list[tuple[Path, dict[str, Any]]] = []
        for manifest_path in self.root.glob("*/manifest.json"):
            try:
                with manifest_path.open("r", encoding="utf-8") as source:
                    manifest = json.load(source)
                if not isinstance(manifest, dict) or not manifest.get("experiment_id"):
                    continue
                records.append((manifest_path.parent, manifest))
            except (OSError, ValueError, TypeError):
                continue
        records.sort(
            key=lambda item: str(item[1].get("created_at") or ""),
            reverse=True,
        )
        return records

    @staticmethod
    def write_manifest(directory: Path, manifest: dict[str, Any]) -> None:
        manifest_path = directory / "manifest.json"
        temporary_path = directory / ".manifest.json.tmp"
        with temporary_path.open("w", encoding="utf-8", newline="\n") as output:
            json.dump(manifest, output, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary_path, manifest_path)

    def finalize(
        self,
        session: ExperimentSession,
        *,
        completion_status: str,
        sample_count: int,
        raw_line_count: int,
        interruption_reason: str | None = None,
        ended_at: datetime | None = None,
    ) -> dict[str, Any]:
        if completion_status not in ("COMPLETED", "INTERRUPTED"):
            raise ValueError("completion_status must be COMPLETED or INTERRUPTED")
        manifest = dict(session.manifest)
        manifest.update(
            completion_status=completion_status,
            ended_at=(ended_at or datetime.now(timezone.utc)).astimezone().isoformat(),
            interruption_reason=interruption_reason,
            sample_count=sample_count,
            raw_line_count=raw_line_count,
        )
        # Compute the peak from the recorded samples, including samples received
        # after excitation stopped but before the recorder was finalized.
        peak_angle = None
        peak_time = None
        with (session.directory / "telemetry.csv").open(
            "r", encoding="utf-8", newline=""
        ) as telemetry_file:
            for row in csv.DictReader(telemetry_file):
                try:
                    angle = float(row["angle_deg"])
                    sample_time = float(row["time_s"])
                except (KeyError, TypeError, ValueError):
                    continue
                if not (math.isfinite(angle) and math.isfinite(sample_time)):
                    continue
                if peak_angle is None or abs(angle) > abs(peak_angle):
                    peak_angle = angle
                    peak_time = sample_time
        manifest["peak_absolute_angle_deg"] = abs(peak_angle) if peak_angle is not None else None
        manifest["peak_signed_angle_deg"] = peak_angle
        manifest["peak_angle_time_s"] = peak_time
        experiment = manifest.get("experiment")
        target_angle = experiment.get("target_angle_deg") if isinstance(experiment, dict) else None
        if (
            peak_angle is not None
            and isinstance(target_angle, (int, float))
            and math.isfinite(target_angle)
            and target_angle > 0
        ):
            manifest["recorded_peak_above_target_deg"] = max(0.0, abs(peak_angle) - target_angle)
        else:
            manifest["recorded_peak_above_target_deg"] = None
        # This is a whole-recording peak, not yet a post-stop-only peak.

        self.write_manifest(session.directory, manifest)
        write_simscape_input(session.directory, manifest)
        return manifest

    def load(self, source: Path | str) -> LoadedExperiment:
        source_path = Path(source)
        directory = source_path.parent if source_path.name == "manifest.json" else source_path
        manifest_path = directory / "manifest.json"
        with manifest_path.open("r", encoding="utf-8") as manifest_file:
            manifest = json.load(manifest_file)
        if manifest.get("schema_version") != 1:
            raise ValueError(f"Unsupported experiment schema version: {manifest.get('schema_version')}")

        telemetry: list[TelemetrySample] = []
        with (directory / manifest["files"]["normalized_telemetry"]).open(
            "r", encoding="utf-8", newline=""
        ) as csv_file:
            data_source = manifest.get("data_source", {})
            is_real_recording = (
                isinstance(data_source, dict)
                and data_source.get("mode") == DataSourceMode.REAL.value
            )
            reader = csv.DictReader(csv_file)
            columns = tuple(reader.fieldnames or ())
            if columns not in (
                TELEMETRY_CORE_COLUMNS,
                TELEMETRY_COLUMNS,
                LEGACY_TELEMETRY_CORE_COLUMNS,
                LEGACY_TELEMETRY_COLUMNS,
            ):
                raise ValueError("Experiment telemetry columns do not match schema version 1")
            for row in reader:
                is_legacy = "device_time_s" in row
                time_s = (
                    self._optional_float(row.get("device_time_s"))
                    if is_legacy
                    else self._optional_float(row.get("time_s"))
                )
                received_elapsed_s = self._optional_float(row.get("received_elapsed_s"))
                time_s = (
                    time_s
                    if time_s is not None
                    else received_elapsed_s
                    if received_elapsed_s is not None
                    else 0.0
                )
                telemetry.append(
                    TelemetrySample(
                        sample_index=int(row["sample_index"]),
                        elapsed_s=time_s,
                        angle_deg=self._optional_float(row["angle_deg"]),
                        position_mm=self._optional_float(row["position_mm"]),
                        source="saved",
                        device_time_s=time_s if is_real_recording else None,
                        received_at=self._optional_datetime(row.get("received_at_utc")),
                        rx_line_index=self._optional_int(row["source_rx_line_index"]),
                        device_sample_index=(
                            int(row["sample_index"])
                            if is_real_recording and not is_legacy
                            else None
                        ),
                        received_elapsed_s=received_elapsed_s,
                    )
                )
        if manifest.get("completion_status") == "RECORDING":
            raw_path = directory / manifest["files"]["raw_serial_capture"]
            if raw_path.exists():
                with raw_path.open("r", encoding="utf-8") as raw_file:
                    raw_line_count = sum(1 for _ in raw_file)
            else:
                raw_line_count = 0
            manifest = self.finalize(
                ExperimentSession(manifest["experiment_id"], directory, manifest),
                completion_status="INTERRUPTED",
                sample_count=len(telemetry),
                raw_line_count=raw_line_count,
                interruption_reason="application ended before recording was finalized",
            )
        return LoadedExperiment(
            experiment_id=manifest["experiment_id"],
            directory=directory,
            manifest=manifest,
            telemetry=tuple(telemetry),
        )

    @staticmethod
    def _optional_float(value: str | None) -> float | None:
        return None if value in (None, "") else float(value)

    @staticmethod
    def _optional_int(value: str | None) -> int | None:
        return None if value in (None, "") else int(value)

    @staticmethod
    def _optional_datetime(value: str | None) -> datetime | None:
        return None if value in (None, "") else datetime.fromisoformat(value)
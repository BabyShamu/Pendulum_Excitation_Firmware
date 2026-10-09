import csv
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from uuid import uuid4

from pendulum_workbench.domain.models import DataSourceMode, TelemetrySample

TELEMETRY_CORE_COLUMNS = (
    "sample_index",
    "source_rx_line_index",
    "device_time_s",
    "received_elapsed_s",
    "position_mm",
    "angle_deg",
    "quality",
)
TELEMETRY_COLUMNS = TELEMETRY_CORE_COLUMNS + ("received_at_utc",)
TELEMETRY_SCHEMA_VERSION = 2


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
    ) -> ExperimentSession:
        experiment_id = str(uuid4())
        directory = self.root / experiment_id
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
            "created_at": created_at.isoformat(),
            "started_at": created_at.isoformat(),
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
                "receive_elapsed_s": "monotonic elapsed time since serial connection or mock source start",
                "received_at_utc": "desktop receive timestamp for each normalized sample",
                "device_time_s": "firmware time when present; null for mock telemetry",
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
        self.write_manifest(directory, manifest)
        return ExperimentSession(experiment_id, directory, manifest)

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
            ended_at=(ended_at or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(),
            interruption_reason=interruption_reason,
            sample_count=sample_count,
            raw_line_count=raw_line_count,
        )
        self.write_manifest(session.directory, manifest)
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
            reader = csv.DictReader(csv_file)
            columns = tuple(reader.fieldnames or ())
            if columns not in (TELEMETRY_CORE_COLUMNS, TELEMETRY_COLUMNS):
                raise ValueError("Experiment telemetry columns do not match schema version 1")
            for row in reader:
                elapsed_s = float(row["received_elapsed_s"])
                telemetry.append(
                    TelemetrySample(
                        sample_index=int(row["sample_index"]),
                        elapsed_s=elapsed_s,
                        angle_deg=self._optional_float(row["angle_deg"]),
                        position_mm=self._optional_float(row["position_mm"]),
                        source="saved",
                        device_time_s=self._optional_float(row["device_time_s"]),
                        received_at=self._optional_datetime(row.get("received_at_utc")),
                        rx_line_index=self._optional_int(row["source_rx_line_index"]),
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
import csv
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pendulum_workbench.domain.models import DataSourceMode
from pendulum_workbench.experiments.repository import (
    TELEMETRY_COLUMNS,
    ExperimentRepository,
)


class ExperimentRepositoryTests(unittest.TestCase):
    def test_creates_unique_experiment_folders_with_explicit_unknown_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = ExperimentRepository(Path(temporary_directory))
            first = repository.create(
                name="free decay",
                notes="release from a small angle",
                data_source=DataSourceMode.MOCK,
                serial_port=None,
                created_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
            )
            second = repository.create(
                name="",
                notes="",
                data_source=DataSourceMode.REAL,
                serial_port="COM6",
            )

            self.assertNotEqual(first.experiment_id, second.experiment_id)
            self.assertTrue((first.directory / "manifest.json").exists())
            self.assertTrue((first.directory / "serial_capture.log").exists())
            self.assertTrue((first.directory / "telemetry.csv").exists())
            self.assertTrue((first.directory / "derived").is_dir())
            self.assertEqual(first.manifest["completion_status"], "RECORDING")
            self.assertEqual(first.manifest["data_source"]["mode"], "Mock")
            self.assertIsNone(first.manifest["firmware_version"])
            self.assertIn("unknown", first.manifest["firmware_version_status"])
            self.assertEqual(
                first.manifest["configuration"]["physical_parameters"]["pendulum_length_m"],
                {"value": None, "status": "TBD", "source": None},
            )
            self.assertEqual(second.manifest["data_source"]["serial_port"], "COM6")

    def test_reopens_normalized_telemetry_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = ExperimentRepository(Path(temporary_directory))
            session = repository.create(
                name="tracking run",
                notes="steady-state sample",
                data_source=DataSourceMode.REAL,
                serial_port="COM6",
                created_at=datetime(2026, 10, 9, tzinfo=timezone.utc),
            )
            with (session.directory / "telemetry.csv").open(
                "w", encoding="utf-8", newline=""
            ) as telemetry_file:
                writer = csv.writer(telemetry_file)
                writer.writerow(TELEMETRY_COLUMNS)
                writer.writerow(
                    [1, 42, 0.02, 0.1, 1.25, -3.5, "valid", "2026-10-09T00:00:00+00:00"]
                )

            reopened = repository.load(session.directory)

            self.assertEqual(reopened.experiment_id, session.experiment_id)
            self.assertEqual(reopened.manifest["notes"], "steady-state sample")
            self.assertEqual(len(reopened.telemetry), 1)
            sample = reopened.telemetry[0]
            self.assertEqual(sample.sample_index, 1)
            self.assertEqual(sample.rx_line_index, 42)
            self.assertEqual(sample.elapsed_s, 0.1)
            self.assertEqual(sample.device_time_s, 0.02)
            self.assertEqual(sample.position_mm, 1.25)
            self.assertEqual(sample.angle_deg, -3.5)
            self.assertEqual(sample.source, "saved")
            self.assertEqual(sample.received_at, datetime(2026, 10, 9, tzinfo=timezone.utc))

    def test_reopening_an_unfinalized_manifest_marks_it_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = ExperimentRepository(Path(temporary_directory))
            session = repository.create(
                name="crash recovery",
                notes="",
                data_source=DataSourceMode.MOCK,
                serial_port=None,
            )
            with (session.directory / "telemetry.csv").open(
                "a", encoding="utf-8", newline=""
            ) as telemetry_file:
                csv.writer(telemetry_file, lineterminator="\n").writerow(
                    [1, "", "", 0.1, 2.0, -1.0, "valid", "2026-10-09T00:00:00+00:00"]
                )
            with (session.directory / "serial_capture.log").open("a", encoding="utf-8") as raw_file:
                raw_file.write('{"rx_line_index":1}\n')

            reopened = repository.load(session.directory)

            self.assertEqual(reopened.manifest["completion_status"], "INTERRUPTED")
            self.assertEqual(reopened.manifest["sample_count"], 1)
            self.assertEqual(reopened.manifest["raw_line_count"], 1)
            self.assertIn("ended before recording was finalized", reopened.manifest["interruption_reason"])


if __name__ == "__main__":
    unittest.main()
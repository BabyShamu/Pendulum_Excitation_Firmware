"""Target crossing metrics from physical telemetry."""
import tempfile
import unittest
from pathlib import Path

from pendulum_workbench.domain.models import DataSourceMode
from pendulum_workbench.experiments.repository import ExperimentRepository
from pendulum_workbench.experiments.target_metrics import target_metrics


class TargetMetricsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        (self.path / "telemetry.csv").write_text(
            "time_s,angle_deg,position_mm\n"
            "100.0,5,0\n"
            "100.2,15,0\n"
            "100.4,35,0\n"
            "100.6,-38,0\n",
            encoding="utf-8",
        )

    def test_interpolates_first_crossing(self):
        result = target_metrics(self.path, {"experiment": {"target_angle_deg": 30}})
        self.assertTrue(result["target_achieved"])
        self.assertAlmostEqual(result["target_time_s"], 0.35)

    def test_no_target_is_not_failure(self):
        result = target_metrics(self.path, {"experiment": {}})
        self.assertIsNone(result["target_achieved"])
        self.assertIsNone(result["target_time_s"])

    def test_recorded_monotonic_target_overrides_post_stop_telemetry(self):
        manifest = {"experiment": {
            "target_angle_deg": 30,
            "actual_elapsed_duration_s": 14.3,
            "target_time_source": "workbench_monotonic_motor_start_ack",
            "target_achieved": True,
            "target_time_s": 14.27,
        }}
        result = target_metrics(self.path, manifest)
        self.assertTrue(result["target_achieved"])
        self.assertAlmostEqual(result["target_time_s"], 14.27)

    def test_post_stop_angle_does_not_create_target(self):
        manifest = {"experiment": {
            "target_angle_deg": 30,
            "actual_elapsed_duration_s": 10.0,
            "target_time_source": "workbench_monotonic_motor_start_ack",
            "target_achieved": False,
            "target_time_s": None,
        }}
        result = target_metrics(self.path, manifest)
        self.assertFalse(result["target_achieved"])
        self.assertIsNone(result["target_time_s"])

    def test_inconsistent_recorded_target_is_not_silently_accepted(self):
        manifest = {"experiment": {
            "target_angle_deg": 30,
            "actual_elapsed_duration_s": 14.3,
            "target_time_source": "workbench_monotonic_motor_start_ack",
            "target_achieved": True,
            "target_time_s": 14.83,
        }}
        result = target_metrics(self.path, manifest)
        self.assertIsNone(result["target_achieved"])
        self.assertEqual(result["time_source"], "invalid_recorded_event")

    def test_completed_51_15_degree_run_persists_target_status_after_reload(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            repository = ExperimentRepository(Path(temporary_directory))
            session = repository.create(
                name="completed target",
                notes="",
                data_source=DataSourceMode.REAL,
                serial_port="COM6",
                experiment_metadata={
                    "target_angle_deg": 50.0,
                    "actual_elapsed_duration_s": 12.1,
                    "target_time_source": "workbench_monotonic_motor_start_ack",
                    "target_achieved": True,
                    "target_time_s": 12.075,
                    "measured_initial_angle_deg": 16.35,
                },
            )
            with (session.directory / "telemetry.csv").open(
                "a", encoding="utf-8", newline=""
            ) as telemetry_file:
                telemetry_file.write(
                    "1,0.0,16.35,0,,,,valid\n"
                    "2,12.075,51.15,0,,,,valid\n"
                )
            repository.finalize(
                session,
                completion_status="COMPLETED",
                sample_count=2,
                raw_line_count=2,
            )

            reloaded = repository.load(session.directory)
            result = target_metrics(session.directory, reloaded.manifest)
            self.assertTrue(result["target_achieved"])
            self.assertAlmostEqual(result["target_time_s"], 12.075)
            self.assertEqual(reloaded.manifest["completion_status"], "COMPLETED")

            listed = repository.list_manifests()
            self.assertEqual(len(listed), 1)
            self.assertTrue(target_metrics(listed[0][0], listed[0][1])["target_achieved"])

    def test_unreached_target(self):
        result = target_metrics(self.path, {"experiment": {"target_angle_deg": 65}})
        self.assertFalse(result["target_achieved"])
        self.assertIsNone(result["target_time_s"])


if __name__ == "__main__":
    unittest.main()

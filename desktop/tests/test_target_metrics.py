"""Target crossing metrics from physical telemetry."""
import tempfile
import unittest
from pathlib import Path

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

    def test_unreached_target(self):
        result = target_metrics(self.path, {"experiment": {"target_angle_deg": 65}})
        self.assertFalse(result["target_achieved"])
        self.assertIsNone(result["target_time_s"])


if __name__ == "__main__":
    unittest.main()

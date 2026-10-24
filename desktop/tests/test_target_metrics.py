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

    def test_unreached_target(self):
        result = target_metrics(self.path, {"experiment": {"target_angle_deg": 65}})
        self.assertFalse(result["target_achieved"])
        self.assertIsNone(result["target_time_s"])


if __name__ == "__main__":
    unittest.main()

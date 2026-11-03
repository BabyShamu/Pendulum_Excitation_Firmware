import tempfile
import unittest
from pathlib import Path

from pendulum_workbench.experiments.target_metrics import target_metrics


class MaximumAngleMetricsTests(unittest.TestCase):
    def test_uses_validated_samples_and_fixed_success_threshold(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "telemetry.csv").write_text(
                "time_s,angle_deg,quality\n"
                "0,35.1,valid\n"
                "1,35.8,valid\n"
                "2,117.4,rejected\n"
                "3,36.5,valid\n"
                "4,37.2,valid\n",
                encoding="utf-8",
            )
            result = target_metrics(path, {"experiment": {}})
        self.assertEqual(result["maximum_angle_deg"], 37.2)
        self.assertEqual(result["outcome"], "COMPLETED")

    def test_threshold_and_missing_samples(self):
        self.assertEqual(
            target_metrics(Path(tempfile.mkdtemp()), {
                "maximum_angle_deg": 90.0,
            })["outcome"],
            "SUCCESS",
        )
        self.assertEqual(
            target_metrics(Path(tempfile.mkdtemp()), {
                "maximum_angle_deg": 89.9,
            })["outcome"],
            "COMPLETED",
        )
        self.assertEqual(
            target_metrics(Path(tempfile.mkdtemp()), {"experiment": {}})["outcome"],
            "INVALID",
        )

    def test_time_to_success_is_projected_only_for_successful_runs(self):
        result = target_metrics(Path(tempfile.mkdtemp()), {
            "experiment": {
                "outcome": "SUCCESS",
                "maximum_angle_deg": 90.4,
                "time_to_success_s": 8.42,
            }
        })
        self.assertEqual(result["time_to_success_s"], 8.42)
        self.assertEqual(result["target_time_s"], 8.42)
        self.assertEqual(result["target_angle_deg"], 90.0)

        completed = target_metrics(Path(tempfile.mkdtemp()), {
            "experiment": {
                "outcome": "COMPLETED",
                "maximum_angle_deg": 74.2,
                "time_to_success_s": 8.42,
            }
        })
        self.assertIsNone(completed["time_to_success_s"])
        self.assertIsNone(completed["target_time_s"])

        missing = target_metrics(Path(tempfile.mkdtemp()), {
            "experiment": {"outcome": "SUCCESS", "maximum_angle_deg": 91.0}
        })
        self.assertIsNone(missing["time_to_success_s"])

        invalid = target_metrics(Path(tempfile.mkdtemp()), {
            "experiment": {"outcome": "SUCCESS", "time_to_success_s": "not-a-number"}
        })
        self.assertIsNone(invalid["time_to_success_s"])

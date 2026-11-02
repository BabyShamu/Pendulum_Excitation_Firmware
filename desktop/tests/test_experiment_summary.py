"""Regression tests for the shared Library/Excel experiment projection."""
import tempfile
import unittest
from pathlib import Path

from pendulum_workbench.experiments.experiment_summary import experiment_summary


class ExperimentSummaryTests(unittest.TestCase):
    def test_projection_preserves_raw_manifest_and_exposes_shared_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "recording"
            directory.mkdir()
            manifest = {
                "experiment_id": "example-id",
                "name": "Sine trial",
                "created_at": "2026-11-02T12:00:00",
                "completion_status": "COMPLETED",
                "peak_absolute_angle_deg": 82.62,
                "experiment": {
                    "experiment_mode": "VERTICAL_SINUSOIDAL_EXCITATION",
                    "measured_initial_angle_deg": 14.8,
                    "actual_elapsed_duration_s": 12.5,
                    "maximum_angle_deg": 82.62,
                },
            }
            before = repr(manifest)
            summary = experiment_summary(directory, manifest)
            self.assertEqual(summary["experiment_id"], "example-id")
            self.assertEqual(summary["mode"], "Sinusoidal")
            self.assertEqual(summary["duration_s"], 12.5)
            self.assertEqual(summary["initial_angle_deg"], 14.8)
            self.assertEqual(summary["measured_peak_angle_deg"], 82.62)
            self.assertEqual(summary["maximum_angle_deg"], 82.62)
            self.assertEqual(summary["outcome"], "COMPLETED")
            self.assertFalse(summary["target_achieved"])
            self.assertEqual(summary["simulation_status"], "Not simulated")
            self.assertEqual(repr(manifest), before)
            self.assertFalse((directory / "manifest.json").exists())

    def test_explicit_repository_root_is_preserved_by_workbench_ui(self):
        from pendulum_workbench.application.controller import WorkbenchController
        from pendulum_workbench.experiments.repository import ExperimentRepository
        from pendulum_workbench.ui.main_window import MainWindow
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            repository = ExperimentRepository(Path(temp))
            self.assertTrue(repository.explicit_root)
            controller = WorkbenchController(experiment_repository=repository)
            try:
                window = MainWindow(controller)
                try:
                    self.assertEqual(repository.root, Path(temp))
                    self.assertEqual(controller.experiment_recorder.repository.root, Path(temp))
                finally:
                    window.close()
            finally:
                controller.shutdown()

    def test_invalid_values_do_not_become_metrics(self):
        with tempfile.TemporaryDirectory() as temp:
            manifest = {
                "experiment": {
                    "measured_initial_angle_deg": True,
                    "commanded_duration_s": float("nan"),
                }
            }
            result = experiment_summary(Path(temp), manifest)
            self.assertIsNone(result["initial_angle_deg"])
            self.assertIsNone(result["duration_s"])
            self.assertIsNone(result["maximum_angle_deg"])
            self.assertIsNone(result["target_achieved"])


if __name__ == "__main__":
    unittest.main()

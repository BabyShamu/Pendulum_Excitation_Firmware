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
            self.assertIsNone(summary["time_to_success_s"])
            self.assertEqual(summary["simulation_status"], "Not simulated")
            self.assertEqual(repr(manifest), before)
            self.assertFalse((directory / "manifest.json").exists())

    def test_projection_exposes_time_to_success_for_successful_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "recording"
            directory.mkdir()
            manifest = {
                "experiment_id": "success-id",
                "name": "AR success",
                "created_at": "2026-11-03T10:00:00",
                "completion_status": "COMPLETED",
                "peak_absolute_angle_deg": 90.4,
                "experiment": {
                    "experiment_mode": "AUTO_RESONANCE_PARAMETRIC",
                    "measured_initial_angle_deg": 12.4,
                    "actual_elapsed_duration_s": 8.6,
                    "maximum_angle_deg": 90.4,
                    "outcome": "SUCCESS",
                    "completion_reason": "target_reached",
                    "time_to_success_s": 8.42,
                },
            }
            summary = experiment_summary(directory, manifest)
            self.assertEqual(summary["outcome"], "SUCCESS")
            self.assertTrue(summary["target_achieved"])
            self.assertEqual(summary["time_to_success_s"], 8.42)

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

    def test_library_table_and_details_share_maximum_and_completion(self):
        from PySide6.QtWidgets import QApplication
        from pendulum_workbench.application.controller import WorkbenchController
        from pendulum_workbench.experiments.repository import ExperimentRepository
        from pendulum_workbench.ui.main_window import MainWindow

        import json
        import time as _time

        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / "recording"
            directory.mkdir()
            manifest = {
                "experiment_id": "library-check",
                "name": "Maximum check",
                "created_at": "2026-11-02T12:00:00",
                "completion_status": "COMPLETED",
                "peak_absolute_angle_deg": 60.0,
                "files": {
                    "raw_serial_capture": "serial_capture.log",
                    "normalized_telemetry": "telemetry.csv",
                    "derived_data_directory": "derived/",
                },
                "experiment": {
                    "maximum_angle_deg": 95.0,
                    "completion_reason": "duration_elapsed",
                },
            }
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (directory / "telemetry.csv").write_text(
                "sample_index,time_s,angle_deg,position_mm,quality\n",
                encoding="utf-8",
            )
            repository = ExperimentRepository(Path(temp))
            controller = WorkbenchController(experiment_repository=repository)
            try:
                window = MainWindow(controller)
                try:
                    page = window.manager_page
                    page.ensure_loaded()  # triggers the deferred initial load
                    deadline = _time.monotonic() + 3.0
                    while page.table.rowCount() != 1 and _time.monotonic() < deadline:
                        app.processEvents()
                        _time.sleep(0.005)
                    self.assertEqual(page.table.rowCount(), 1)
                    self.assertEqual(page.table.item(0, 5).text(), "95.00°")
                    self.assertEqual(page.table.item(0, 7).text(), "SUCCESS")
                    self.assertEqual(page.table.item(0, 8).text(), "COMPLETED")
                    page.table.selectRow(0)
                    app.processEvents()
                    self.assertEqual(page.detail_labels["Maximum angle"].text(), "95.00°")
                    self.assertEqual(page.detail_labels["Completion"].text(), "COMPLETED")
                    self.assertEqual(
                        page.detail_labels["Completion reason"].text(), "duration_elapsed"
                    )
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

"""Experiment Manager responsiveness and cache correctness tests."""
import csv
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.experiments.loader import ExperimentListLoader
from pendulum_workbench.experiments.repository import ExperimentRepository
from pendulum_workbench.experiments.stats_cache import ExperimentStatsCache
from pendulum_workbench.ui.pages.experiment_manager_page import ExperimentManagerPage


def _write_experiment(
    root: Path,
    index: int,
    *,
    name: str | None = None,
    max_angle: float = 45.0,
    with_simulation: bool = False,
    telemetry_rows: int = 60,
    corrupt_manifest: bool = False,
) -> Path:
    directory = root / f"2026-10-{10 + (index % 20):02d}_AR_{index:04d}"
    directory.mkdir(parents=True)
    (directory / "derived").mkdir(exist_ok=True)
    manifest = {
        "schema_version": 1,
        "telemetry_schema_version": 3,
        "experiment_id": f"id-{index:04d}",
        "name": name or f"AR_test_{index:04d}",
        "created_at": f"2026-10-{10 + (index % 20):02d}T10:{index % 60:02d}:00",
        "completion_status": "COMPLETED",
        "notes": "",
        "data_source": {"mode": "Real STM32", "serial_port": "COM5"},
        "sample_count": telemetry_rows,
        "raw_line_count": telemetry_rows * 2,
        "peak_absolute_angle_deg": max_angle,
        "maximum_angle_deg": max_angle,
        "max_angle_valid": True,
        "outcome": "COMPLETED",
        "files": {
            "raw_serial_capture": "serial_capture.log",
            "normalized_telemetry": "telemetry.csv",
            "derived_data_directory": "derived/",
        },
        "experiment": {
            "experiment_mode": "AUTO_RESONANCE_PARAMETRIC",
            "commanded_amplitude_mm": 7.5,
            "commanded_phase_deg": 22.5,
            "requested_duration_s": 30.0,
            "actual_elapsed_duration_s": 30.1,
            "measured_initial_angle_deg": 12.4,
            "completion_reason": "duration_elapsed",
            "maximum_angle_deg": max_angle,
            "max_angle_valid": True,
            "outcome": "COMPLETED",
            "time_to_success_s": None,
        },
    }
    if corrupt_manifest:
        (directory / "manifest.json").write_text("{ not valid json", encoding="utf-8")
    else:
        (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (directory / "serial_capture.log").write_text("", encoding="utf-8")
    with (directory / "telemetry.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["sample_index", "time_s", "angle_deg", "position_mm",
                         "source_rx_line_index", "received_elapsed_s", "received_at_utc", "quality"])
        for row in range(telemetry_rows):
            writer.writerow([row, f"{row * 0.02:.3f}", f"{(row % 90) - 45:.2f}", "0.000",
                             row, f"{row * 0.02:.3f}", "2026-10-10T10:00:00+00:00", "valid"])
    if with_simulation:
        sim = directory / "simulation"
        sim.mkdir()
        (directory / "simulation_input.json").write_text(
            json.dumps({"experiment_id": f"id-{index:04d}"}), encoding="utf-8")
        digest = hashlib.sha256((directory / "simulation_input.json").read_bytes()).hexdigest()
        (sim / "metadata.json").write_text(json.dumps({
            "schema_version": 1, "status": "complete",
            "experiment_id": f"id-{index:04d}",
            "fingerprint": {"input_sha256": digest},
            "simulation_model": "Pendulum_Simscape_Model",
        }), encoding="utf-8")
        with (sim / "simulation.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["time_s", "pendulum_angle_deg"])
            for row in range(telemetry_rows):
                writer.writerow([f"{row * 0.02:.3f}", f"{(row % 80) - 40:.2f}"])
        for artifact in ("simulation.mat", "pendulum_angle.png", "pivot_position.png", "simulation_overview.fig"):
            (sim / artifact).write_bytes(b"synthetic")
    return directory


class ExperimentManagerPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if predicate():
                return True
            time.sleep(0.003)
        self.qt_app.processEvents()
        return predicate()

    def _make_page(self, root: Path):
        controller = WorkbenchController(
            experiment_repository=ExperimentRepository(root)
        )
        page = ExperimentManagerPage(controller)
        self.addCleanup(controller.shutdown)
        self.addCleanup(page.close)
        page.ensure_loaded()  # triggers the deferred initial load
        return page

    def test_open_with_100_plus_experiments_populates_without_blocking(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(120):
                _write_experiment(root, index, with_simulation=index < 30)
            page = self._make_page(root)

            self.assertTrue(
                self.wait_for(lambda: page.table.rowCount() == 120, timeout_s=15.0)
            )
            self.assertFalse(page.loading_indicator.isVisible())

    def test_search_filters_on_cached_metadata_without_disk_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(40):
                _write_experiment(root, index, name=f"trial_{index:02d}")
            _write_experiment(root, 100, name="unique_resonance_run")
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 41))

            page.search.setText("unique_resonance")
            self.assertEqual(page.table.rowCount(), 1)
            self.assertEqual(page.table.item(0, 1).text(), "unique_resonance_run")

            page.search.setText("trial_")
            self.assertEqual(page.table.rowCount(), 40)

            page.search.setText("")
            self.assertEqual(page.table.rowCount(), 41)

    def test_visibility_filter_uses_cached_archive_flag(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(6):
                _write_experiment(root, index)
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 6))

            # Archive one experiment on disk, then refresh.
            target = sorted(root.iterdir())[0]
            manifest_path = target / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["archived"] = True
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            page.refresh()
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 5))

            page.visibility.setCurrentText("Archived")
            self.assertEqual(page.table.rowCount(), 1)
            page.visibility.setCurrentText("All")
            self.assertEqual(page.table.rowCount(), 6)

    def test_selection_shows_details_from_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_experiment(root, 0, max_angle=82.62)
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))

            page.table.selectRow(0)
            self.assertEqual(page.detail_labels["Maximum angle"].text(), "82.62°")
            self.assertEqual(page.detail_labels["Result"].text(), "COMPLETED")

    def test_refresh_preserves_selection(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(5):
                _write_experiment(root, index, name=f"keep_{index}")
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 5))
            page.table.selectRow(2)
            selected_name = page.table.item(2, 1).text()

            page.refresh()
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 5))
            current = page.table.currentRow()
            self.assertGreaterEqual(current, 0)
            self.assertEqual(page.table.item(current, 1).text(), selected_name)

    def test_delete_removes_experiment_and_invalidates_cache(self) -> None:
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(4):
                _write_experiment(root, index)
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 4))
            page.table.selectRow(0)
            deleted_dir = Path(page.table.item(0, 0).data(Qt.ItemDataRole.UserRole))

            with patch.object(
                QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes
            ):
                page.delete_selected()
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 3))
            self.assertFalse(deleted_dir.exists())
            self.assertIsNone(page._stats_cache._entries.get(str(deleted_dir)))

    def test_archive_toggles_visibility_and_invalidates_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(3):
                _write_experiment(root, index)
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 3))
            page.table.selectRow(0)

            page.toggle_archive()
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 2))
            page.visibility.setCurrentText("Archived")
            self.assertEqual(page.table.rowCount(), 1)

    def test_cache_invalidates_on_manifest_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = _write_experiment(root, 0, max_angle=40.0)
            cache = ExperimentStatsCache()
            manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))

            first = cache.summary(directory, manifest)
            self.assertEqual(first["maximum_angle_deg"], 40.0)

            # Modify the manifest on disk; the cache must not return stale data.
            manifest["experiment"]["maximum_angle_deg"] = 77.7
            manifest["maximum_angle_deg"] = 77.7
            time.sleep(0.02)  # ensure a distinct mtime
            (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            second = cache.summary(directory, manifest)
            self.assertEqual(second["maximum_angle_deg"], 77.7)

    def test_cache_invalidates_on_simulation_change(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = _write_experiment(root, 0, with_simulation=True)
            cache = ExperimentStatsCache()

            status, sim_max = cache.simulation(directory)
            self.assertEqual(status.state, "ready")
            self.assertIsNotNone(sim_max)

            # Remove a required simulation artifact; status must become incomplete.
            time.sleep(0.02)
            (directory / "simulation" / "simulation.mat").unlink()
            status2, _ = cache.simulation(directory)
            self.assertEqual(status2.state, "incomplete")

    def test_navigation_away_during_load_does_not_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(60):
                _write_experiment(root, index)
            page = self._make_page(root)
            # Immediately close while the background load is in flight.
            page.close()
            self.qt_app.processEvents()
            time.sleep(0.05)
            self.qt_app.processEvents()
            # No exception and the loader was detached.
            self.assertIsNone(page._loader)

    def test_stale_load_cannot_overwrite_newer_results(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(3):
                _write_experiment(root, index)
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 3))

            # Add an experiment and refresh; an artificially delayed older
            # generation must not overwrite the newer list.
            _write_experiment(root, 99, name="late_addition")
            stale_generation = page._load_generation
            page.refresh()
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 4))
            # Deliver a stale result directly; it must be ignored.
            page._on_records_loaded(stale_generation, [])
            self.assertEqual(page.table.rowCount(), 4)

    def test_missing_and_corrupted_manifests_are_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _write_experiment(root, 0, name="good_one")
            _write_experiment(root, 1, name="corrupted", corrupt_manifest=True)
            # A folder with no manifest at all.
            (root / "empty_folder").mkdir()
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))
            self.assertEqual(page.table.item(0, 1).text(), "good_one")

    def test_loader_thread_delivers_records_and_respects_cancel(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for index in range(5):
                _write_experiment(root, index)
            results = []
            loader = ExperimentListLoader(root, 7)
            loader.finished.connect(lambda generation, records: results.append((generation, records)))
            loader.start()
            loader.wait(5000)
            self.qt_app.processEvents()
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0][0], 7)
            self.assertEqual(len(results[0][1]), 5)

            cancelled = []
            loader2 = ExperimentListLoader(root, 8)
            loader2.finished.connect(lambda generation, records: cancelled.append(records))
            loader2.cancel()
            loader2.start()
            loader2.wait(5000)
            self.qt_app.processEvents()
            self.assertEqual(cancelled, [])


class SimulationResultsMismatchRegressionTests(unittest.TestCase):
    """Reproduce the reported sequence: simulation Ready but Library shows
    'Not simulated' after MATLAB completion, Refresh, and navigation."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if predicate():
                return True
            time.sleep(0.003)
        self.qt_app.processEvents()
        return predicate()

    def _make_page(self, root: Path):
        controller = WorkbenchController(
            experiment_repository=ExperimentRepository(root)
        )
        page = ExperimentManagerPage(controller)
        self.addCleanup(controller.shutdown)
        self.addCleanup(page.close)
        page.ensure_loaded()
        return page

    @staticmethod
    def _create_simulation(directory: Path, experiment_id: str, peak: float) -> None:
        sim = directory / "simulation"
        sim.mkdir(exist_ok=True)
        (directory / "simulation_input.json").write_text(
            json.dumps({"experiment_id": experiment_id}), encoding="utf-8")
        digest = hashlib.sha256(
            (directory / "simulation_input.json").read_bytes()
        ).hexdigest()
        (sim / "metadata.json").write_text(json.dumps({
            "schema_version": 1, "status": "complete",
            "experiment_id": experiment_id,
            "fingerprint": {"input_sha256": digest},
            "simulation_model": "Pendulum_Simscape_Model",
        }), encoding="utf-8")
        with (sim / "simulation.csv").open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            writer.writerow(["time_s", "pendulum_angle_deg"])
            writer.writerow(["0.0", "10.0"])
            writer.writerow(["1.0", f"{peak:.2f}"])
        for artifact in ("simulation.mat", "pendulum_angle.png",
                         "pivot_position.png", "simulation_overview.fig"):
            (sim / artifact).write_bytes(b"synthetic")

    def _table_row_for(self, page, directory: Path) -> int:
        for row in range(page.table.rowCount()):
            item = page.table.item(row, 0)
            if item is not None and Path(item.data(Qt.ItemDataRole.UserRole)) == directory:
                return row
        return -1

    def test_simulation_appears_after_matlab_completion_refresh_and_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = _write_experiment(root, 0, name="sim_target", with_simulation=False)
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))

            # 1-2. Loaded with no simulation; cache holds 'Not simulated'.
            row = self._table_row_for(page, directory)
            self.assertEqual(page.table.item(row, 9).text(), "Not simulated")
            self.assertEqual(page.table.item(row, 6).text(), "—")

            # 3. Create valid simulation files (as MATLAB does).
            self._create_simulation(directory, "id-0000", peak=88.5)

            # 4. Trigger MATLAB completion -> refresh().
            page._active_simulation_folder = directory
            page._on_matlab_finished(0, None)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))

            # 5. UI displays Saved and the correct simulated maximum angle.
            row = self._table_row_for(page, directory)
            self.assertEqual(page.table.item(row, 9).text(), "Saved")
            self.assertEqual(page.table.item(row, 6).text(), "88.50°")

            # 6. Manual Refresh keeps the values correct.
            page.refresh()
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))
            row = self._table_row_for(page, directory)
            self.assertEqual(page.table.item(row, 9).text(), "Saved")
            self.assertEqual(page.table.item(row, 6).text(), "88.50°")

            # 7. Navigate away and back (hide/show) keeps the values correct.
            page.close()
            page2 = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page2.table.rowCount() == 1))
            row = self._table_row_for(page2, directory)
            self.assertEqual(page2.table.item(row, 9).text(), "Saved")
            self.assertEqual(page2.table.item(row, 6).text(), "88.50°")

    def test_rerun_simulation_updates_displayed_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = _write_experiment(root, 0, with_simulation=False)
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))

            self._create_simulation(directory, "id-0000", peak=88.5)
            page._active_simulation_folder = directory
            page._on_matlab_finished(0, None)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))
            row = self._table_row_for(page, directory)
            self.assertEqual(page.table.item(row, 6).text(), "88.50°")

            # Re-run with a different peak; the display must update.
            time.sleep(0.02)
            self._create_simulation(directory, "id-0000", peak=91.25)
            page._on_matlab_finished(0, None)
            row = self._table_row_for(page, directory)
            self.assertTrue(
                self.wait_for(lambda: page.table.item(row, 6).text() == "91.25°")
            )

    def test_deleted_simulation_output_reverts_to_not_simulated(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = _write_experiment(root, 0, with_simulation=False)
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))

            self._create_simulation(directory, "id-0000", peak=88.5)
            page._active_simulation_folder = directory
            page._on_matlab_finished(0, None)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))
            row = self._table_row_for(page, directory)
            self.assertEqual(page.table.item(row, 9).text(), "Saved")

            # Delete a required artifact; the status must become Incomplete.
            time.sleep(0.02)
            (directory / "simulation" / "simulation.mat").unlink()
            page.refresh()
            row = self._table_row_for(page, directory)
            self.assertTrue(
                self.wait_for(lambda: page.table.item(row, 9).text() == "Incomplete")
            )

    def test_modified_simulation_csv_updates_maximum(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            directory = _write_experiment(root, 0, with_simulation=False)
            page = self._make_page(root)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))

            self._create_simulation(directory, "id-0000", peak=88.5)
            page._active_simulation_folder = directory
            page._on_matlab_finished(0, None)
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))
            row = self._table_row_for(page, directory)
            self.assertEqual(page.table.item(row, 6).text(), "88.50°")

            # Modify simulation.csv directly; the new peak must appear.
            time.sleep(0.02)
            with (directory / "simulation" / "simulation.csv").open(
                "w", encoding="utf-8", newline=""
            ) as handle:
                writer = csv.writer(handle, lineterminator="\n")
                writer.writerow(["time_s", "pendulum_angle_deg"])
                writer.writerow(["0.0", "95.75"])
            page.refresh()
            row = self._table_row_for(page, directory)
            self.assertTrue(
                self.wait_for(lambda: page.table.item(row, 6).text() == "95.75°")
            )


if __name__ == "__main__":
    unittest.main()

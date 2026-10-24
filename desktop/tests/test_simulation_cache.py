"""Regression checks for Workbench recognition of cached MATLAB simulations."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pendulum_workbench.experiments.simulation_cache import inspect_simulation


class SavedSimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / "manifest.json").write_text(
            json.dumps({"experiment_id": "test-001"}), encoding="utf-8"
        )
        self.input_path = self.folder / "simulation_input.json"
        self.input_path.write_text(
            '{"experiment_id":"test-001","initial_angle_deg":10}',
            encoding="utf-8"
        )

    def _save_simulation(self):
        sim = self.folder / "simulation"
        sim.mkdir()
        fingerprint = hashlib.sha256(self.input_path.read_bytes()).hexdigest()
        (sim / "metadata.json").write_text(json.dumps({
            "schema_version": 1, "status": "complete",
            "experiment_id": "test-001",
            "fingerprint": {"input_sha256": fingerprint},
        }), encoding="utf-8")
        for file in ("simulation.mat", "simulation.csv", "pendulum_angle.png",
                     "pivot_position.png", "simulation_overview.fig"):
            (sim / file).write_bytes(b"mock-test-content")
        return sim

    def test_missing_simulation(self):
        self.assertEqual(inspect_simulation(self.folder).state, "missing")

    def test_saved_simulation(self):
        self._save_simulation()
        status = inspect_simulation(self.folder)
        self.assertEqual(status.state, "ready")
        self.assertTrue(status.folder.is_dir())

    def test_stale_when_inputs_change(self):
        self._save_simulation()
        self.input_path.write_text('{"initial_angle_deg":12}', encoding="utf-8")
        self.assertEqual(inspect_simulation(self.folder).state, "stale")

    def test_missing_simulation_file(self):
        sim = self._save_simulation()
        (sim / "simulation.mat").unlink()
        self.assertEqual(inspect_simulation(self.folder).state, "incomplete")

    def test_wrong_experiment_id(self):
        self._save_simulation()
        (self.folder / "manifest.json").write_text(
            json.dumps({"experiment_id": "another"}), encoding="utf-8"
        )
        self.assertEqual(inspect_simulation(self.folder).state, "stale")


if __name__ == "__main__":
    unittest.main()

"""Regression tests for stationary-pivot Free Swing experiment metadata."""
import json
import tempfile
import unittest
from pathlib import Path

from pendulum_workbench.domain.experiment_run import FreeSwingDefinition, ExperimentMode
from pendulum_workbench.experiments.simscape_export import write_simscape_input


class FreeSwingDefinitionTests(unittest.TestCase):
    def test_free_swing_has_no_excitation_parameters(self):
        definition = FreeSwingDefinition(20.0, label="baseline", notes="release from latch")
        definition.validate()
        self.assertEqual(definition.mode, ExperimentMode.FREE_SWING)
        self.assertTrue(definition.partial_name().startswith("baseline_FS_"))
        self.assertIn("Theta", definition.final_name(-12.5))
        data = definition.manifest_values(measured_initial_angle_deg=-12.5)
        self.assertEqual(data["experiment_mode"], "FREE_SWING")
        self.assertEqual(data["measured_initial_angle_deg"], -12.5)
        self.assertEqual(data["requested_duration_s"], 20.0)
        self.assertIsNone(data["commanded_amplitude_mm"])
        self.assertIsNone(data["commanded_frequency_hz"])
        self.assertIsNone(data["commanded_phase_deg"])
        self.assertIsNone(data["target_angle_deg"])

    def test_duration_must_be_positive(self):
        for duration in (0, -1, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                with self.assertRaises(ValueError):
                    FreeSwingDefinition(duration).validate()

    def test_simscape_export_free_swing_mapping(self):
        metadata = FreeSwingDefinition(15.0).manifest_values(
            measured_initial_angle_deg=9.5,
            actual_elapsed_duration_s=15.02,
            completion_reason="duration_elapsed",
        )
        with tempfile.TemporaryDirectory() as directory:
            path = write_simscape_input(Path(directory), {
                "experiment_id": "test-free-swing",
                "experiment": metadata,
                "completion_status": "COMPLETED",
                "files": {"normalized_telemetry": "telemetry.csv"},
            })
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["control_mode"], "free_swing")
        self.assertEqual(payload["initial_angle_deg"], 9.5)
        self.assertIsNone(payload["excitation_amplitude_mm"])
        self.assertIsNone(payload["excitation_frequency_hz"])
        self.assertEqual(payload["requested_duration_s"], 15.0)


if __name__ == "__main__":
    unittest.main()

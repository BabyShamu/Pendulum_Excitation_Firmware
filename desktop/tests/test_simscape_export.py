import json
import tempfile
import unittest
from pathlib import Path

from pendulum_workbench.domain.models import DataSourceMode
from pendulum_workbench.experiments.repository import ExperimentRepository


class SimscapeExportTests(unittest.TestCase):
    def test_finalization_exports_physical_run_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            repository = ExperimentRepository(Path(tmp))
            session = repository.create(
                name="Sweep",
                notes="",
                data_source=DataSourceMode.MOCK,
                serial_port=None,
                experiment_metadata={
                    "experiment_mode": "VERTICAL_SINUSOIDAL_EXCITATION",
                    "measured_initial_angle_deg": -12.5,
                    "commanded_amplitude_mm": 7.5,
                    "commanded_frequency_hz": 1.85,
                    "requested_duration_s": 30.0,
                    "actual_elapsed_duration_s": 12.6,
                    "completion_reason": "target_angle_reached",
                },
            )
            repository.finalize(
                session, completion_status="COMPLETED",
                sample_count=3, raw_line_count=0,
            )
            exported = json.loads(
                (session.directory / "simulation_input.json").read_text(encoding="utf-8")
            )
            self.assertEqual(exported["control_mode"], "sinusoidal")
            self.assertEqual(exported["initial_angle_deg"], -12.5)
            self.assertEqual(exported["excitation_amplitude_mm"], 7.5)
            self.assertEqual(exported["excitation_frequency_hz"], 1.85)
            self.assertEqual(exported["actual_duration_s"], 12.6)
            self.assertIsNone(exported["initial_angular_velocity_rad_s"])
            self.assertIsNone(exported["excitation_phase_rad"])
            self.assertEqual(exported["telemetry_file"], "telemetry.csv")


if __name__ == "__main__":
    unittest.main()

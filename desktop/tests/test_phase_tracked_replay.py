from pathlib import Path
import csv
import math
import tempfile
import unittest

from pendulum_workbench.domain.phase_tracked_replay import load_trace, replay_trace


PROJECT_ROOT = Path(__file__).resolve().parents[2]
TRACE_PATH = PROJECT_ROOT / "recordings" / "stage4_closed_loop_inverted_growth_01.csv"
LOW_CONFIDENCE_TRACE = PROJECT_ROOT / "recordings" / "stage4_closed_loop_tracking_01.csv"


class PhaseTrackedReplayTests(unittest.TestCase):
    def test_loads_controller_rows_after_putty_log_preamble(self) -> None:
        rows = load_trace(TRACE_PATH)

        self.assertGreater(len(rows), 500)
        self.assertAlmostEqual(rows[0].time_s, 0.005)
        self.assertGreater(rows[-1].time_s, 16.0)

    def test_replay_separates_controller_difference_from_sample_smoothness(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "phase_replay.csv"
            report = replay_trace(TRACE_PATH, output_path=output_path)
            self.assertTrue(output_path.exists())
            with output_path.open("r", encoding="utf-8", newline="") as source:
                header = next(csv.reader(source))

        self.assertGreater(report["compared_samples"], 500)
        self.assertGreater(report["existing_autores_smoothness"]["direction_reversals"], 0)
        self.assertGreater(report["phase_tracked_sine_smoothness"]["direction_reversals"], 0)
        self.assertGreaterEqual(report["max_abs_controller_difference_mm"], 0.0)
        self.assertGreaterEqual(report["rms_controller_difference_mm"], 0.0)
        self.assertNotEqual(
            report["max_abs_controller_difference_mm"],
            report["existing_autores_smoothness"]["max_abs_sample_delta_mm"],
        )
        self.assertGreater(report["measured_phase_samples"], 0)
        self.assertGreater(report["max_abs_phase_step_rad"], 0.0)
        self.assertLessEqual(report["phase_tracked_sine_smoothness"]["target_max_mm"], 7.5)
        self.assertGreaterEqual(report["phase_tracked_sine_smoothness"]["target_min_mm"], -7.5)
        self.assertNotEqual(report["omega_2x_min_rad_s"], "none")
        self.assertAlmostEqual(report["smoothness_comparison_sample_period_s"], 0.02)
        self.assertAlmostEqual(report["phase_target_to_angle_frequency_ratio"], 2.0, delta=0.15)
        self.assertGreater(report["phase_acquisition_time_s"], 0.0)
        self.assertGreater(report["confidence_loss_episodes"], 0)
        self.assertGreater(report["phase_magnitude_q10"], report["phase_magnitude_threshold"])
        self.assertAlmostEqual(report["phase_magnitude_threshold"], 0.05)
        self.assertTrue(
            {
                "time_s",
                "theta_deg",
                "actual_mm",
                "direct_phase_signal",
                "quadrature_phase_signal",
                "phase_magnitude",
                "phase_raw_rad",
                "phase_unwrapped_rad",
                "phase_tracked_rad",
                "omega_2x_rad_s",
                "phase_status",
                "phase_offset_deg",
                "normalized_sine",
                "phase_target_mm",
                "phase_motor_target_mm",
                "existing_autores_target_mm",
            }.issubset(header)
        )

    def test_quiet_trace_acquires_later_and_does_not_false_fault_at_250_ms(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "quiet_replay.csv"
            report = replay_trace(LOW_CONFIDENCE_TRACE, output_path=output_path)
            with output_path.open("r", encoding="utf-8", newline="") as source:
                points = list(csv.DictReader(source))

        self.assertGreater(report["phase_acquisition_time_s"], 0.26)
        self.assertGreater(report["phase_fault_time_s"], 0.26)
        self.assertLess(report["compared_samples"], report["rows"])
        self.assertEqual(report["acquisition_samples_required"], 3)
        initial_position = float(points[0]["actual_mm"])
        initial_target = float(points[0]["phase_motor_target_mm"])
        self.assertAlmostEqual(initial_target, initial_position)
        acquiring_points = [point for point in points if point["phase_status"] == "acquiring"]
        self.assertTrue(
            all(
                math.isclose(float(point["phase_motor_target_mm"]), initial_target, abs_tol=1e-9)
                for point in acquiring_points
            )
        )
        first_locked = next(point for point in points if point["phase_status"] == "measured")
        self.assertAlmostEqual(float(first_locked["phase_motor_target_mm"]), initial_target)


if __name__ == "__main__":
    unittest.main()
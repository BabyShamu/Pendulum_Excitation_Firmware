from pathlib import Path
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

    def test_replay_reports_target_comparison_and_phase_continuity(self) -> None:
        report = replay_trace(TRACE_PATH)

        self.assertGreater(report["compared_samples"], 500)
        self.assertGreater(report["autores_motor_target_reversals"], 0)
        self.assertGreater(report["phase_motor_target_reversals"], 0)
        self.assertGreaterEqual(report["max_abs_target_delta_mm"], 0.0)
        self.assertGreaterEqual(report["rms_target_delta_mm"], 0.0)
        self.assertGreater(report["measured_phase_samples"], 0)
        self.assertGreater(report["max_abs_phase_step_rad"], 0.0)
        self.assertLessEqual(report["phase_motor_target_max_mm"], 7.5)
        self.assertGreaterEqual(report["phase_motor_target_min_mm"], -7.5)
        self.assertNotEqual(report["omega_2x_min_rad_s"], "none")

    def test_replay_stops_at_firmware_equivalent_confidence_fault(self) -> None:
        report = replay_trace(LOW_CONFIDENCE_TRACE)

        self.assertNotEqual(report["phase_fault_time_s"], "none")
        self.assertLess(report["compared_samples"], report["rows"])


if __name__ == "__main__":
    unittest.main()
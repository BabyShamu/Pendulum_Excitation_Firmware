import math
import unittest

from pendulum_workbench.domain.phase_tracked_sine import (
    PHASE_TRACKED_MAX_UNCONFIDENT_MS,
    PhaseConfidence,
    PhaseTrackedSineTarget,
    PhaseTracker,
)


class PhaseTrackerTests(unittest.TestCase):
    @staticmethod
    def vector_for_phase(phase_rad: float, magnitude: float = 1.0) -> tuple[float, float]:
        return -magnitude * math.cos(phase_rad), magnitude * math.sin(phase_rad)

    def test_raw_phase_uses_documented_direct_quadrature_sign_convention(self) -> None:
        tracker = PhaseTracker()
        direct, quadrature = self.vector_for_phase(0.75)

        sample = tracker.update(0.0, direct, quadrature)

        self.assertAlmostEqual(sample.phase_raw_rad, 0.75)
        self.assertIsNone(sample.phase_tracked_rad)
        self.assertEqual(sample.status, PhaseConfidence.ACQUIRING)
        self.assertFalse(sample.locked)

    def test_common_positive_gain_preserves_phase_but_unequal_gains_distort_it(self) -> None:
        direct, quadrature = self.vector_for_phase(0.8)
        original = math.atan2(quadrature, -direct)
        common_gain = math.atan2(4.0 * quadrature, -4.0 * direct)
        unequal_gain = math.atan2(4.0 * quadrature, -0.5 * direct)

        self.assertAlmostEqual(original, common_gain)
        self.assertNotAlmostEqual(original, unequal_gain)

        tracker = PhaseTracker()
        sample = tracker.update(0.0, direct, quadrature)
        self.assertAlmostEqual(sample.phase_raw_rad, original)
        self.assertAlmostEqual(sample.phase_magnitude, math.hypot(direct, quadrature))

    def test_initial_low_confidence_does_not_fault_after_timeout(self) -> None:
        tracker = PhaseTracker(max_unconfident_ms=25.0)
        statuses = [tracker.update(time_s, 0.0, 0.0).status for time_s in (0.0, 0.1, 0.3)]

        self.assertEqual(statuses, [PhaseConfidence.ACQUIRING] * 3)
        self.assertFalse(tracker.locked)
        self.assertIsNone(tracker.phase_tracked_rad)

    def test_first_two_credible_samples_do_not_start_excitation(self) -> None:
        tracker = PhaseTracker()
        first = tracker.update(0.0, *self.vector_for_phase(0.0))
        second = tracker.update(0.01, *self.vector_for_phase(0.1))
        third = tracker.update(0.02, *self.vector_for_phase(0.2))

        self.assertEqual(first.status, PhaseConfidence.ACQUIRING)
        self.assertEqual(second.status, PhaseConfidence.ACQUIRING)
        self.assertIsNone(first.phase_tracked_rad)
        self.assertIsNone(second.phase_tracked_rad)
        self.assertEqual(third.status, PhaseConfidence.MEASURED)
        self.assertTrue(third.locked)
        self.assertAlmostEqual(third.phase_tracked_rad, 0.2)
        self.assertEqual(third.acquisition_samples, 3)

    def test_prelock_dropout_resets_progress_but_preserves_phase_history(self) -> None:
        tracker = PhaseTracker()
        tracker.update(0.0, *self.vector_for_phase(0.0))
        accepted_raw = tracker.previous_phase_raw_rad
        tracker.update(0.005, None, None)
        self.assertEqual(tracker.acquisition_count, 0)
        self.assertEqual(tracker.previous_phase_raw_rad, accepted_raw)

        tracker.update(0.01, *self.vector_for_phase(0.1))
        tracker.update(0.015, *self.vector_for_phase(0.15))
        locked = tracker.update(0.02, *self.vector_for_phase(0.2))

        self.assertTrue(locked.locked)
        self.assertEqual(locked.acquisition_samples, 3)

    def test_phase_unwrap_is_continuous_across_pi_boundary(self) -> None:
        tracker = PhaseTracker()
        tracker.update(0.0, *self.vector_for_phase(math.pi - 0.1))
        tracker.update(0.01, *self.vector_for_phase(-math.pi + 0.1))
        sample = tracker.update(0.02, *self.vector_for_phase(-math.pi + 0.3))

        self.assertAlmostEqual(sample.phase_raw_rad, -math.pi + 0.3)
        self.assertAlmostEqual(sample.phase_tracked_rad, math.pi + 0.3, places=6)
        self.assertAlmostEqual(sample.omega_2x_rad_s, 20.0, places=5)

    def test_tracker_filters_rate_and_corrects_phase_without_raw_pass_through(self) -> None:
        tracker = PhaseTracker(omega_filter_tau_s=0.05, phase_correction_gain=0.1)
        tracker.update(0.0, *self.vector_for_phase(0.0))
        tracker.update(0.01, *self.vector_for_phase(0.1))
        tracker.update(0.02, *self.vector_for_phase(0.4))
        second = tracker.update(0.03, *self.vector_for_phase(0.7))

        self.assertAlmostEqual(second.omega_2x_rad_s, 16.111111, places=5)
        self.assertNotAlmostEqual(second.phase_tracked_rad, second.phase_raw_rad)

    def test_low_confidence_propagates_phase_then_faults_after_bounded_interval(self) -> None:
        tracker = PhaseTracker(max_unconfident_ms=25.0)
        tracker.update(0.0, *self.vector_for_phase(0.0))
        tracker.update(0.01, *self.vector_for_phase(0.1))
        tracker.update(0.02, *self.vector_for_phase(0.2))
        before_loss = tracker.phase_tracked_rad

        propagated = tracker.update(0.03, 0.0, 0.0)
        self.assertEqual(propagated.status, PhaseConfidence.PROPAGATED)
        self.assertGreater(propagated.phase_tracked_rad, before_loss)
        self.assertEqual(tracker.update(0.05, 0.0, 0.0).status, PhaseConfidence.PROPAGATED)
        self.assertEqual(tracker.update(0.081, 0.0, 0.0).status, PhaseConfidence.FAULT)
        self.assertAlmostEqual(PHASE_TRACKED_MAX_UNCONFIDENT_MS, 250.0)

    def test_phase_jump_is_rejected_without_target_phase_jump(self) -> None:
        tracker = PhaseTracker(max_phase_rate_rad_s=20.0, max_unconfident_ms=50.0)
        tracker.update(0.0, *self.vector_for_phase(0.0))
        tracker.update(0.01, *self.vector_for_phase(0.05))
        tracker.update(0.02, *self.vector_for_phase(0.1))
        before_jump = tracker.phase_tracked_rad

        accepted_raw = tracker.previous_phase_raw_rad
        accepted_time = tracker.previous_measurement_time_s
        accepted_unwrapped = tracker.measured_phase_unwrapped_rad
        rejected = tracker.update(0.03, *self.vector_for_phase(1.0))

        self.assertEqual(rejected.status, PhaseConfidence.PROPAGATED)
        self.assertLess(abs(rejected.phase_tracked_rad - before_jump), 0.5)
        self.assertEqual(tracker.previous_phase_raw_rad, accepted_raw)
        self.assertEqual(tracker.previous_measurement_time_s, accepted_time)
        self.assertEqual(tracker.measured_phase_unwrapped_rad, accepted_unwrapped)

    def test_reacquisition_corrects_propagated_phase_gradually(self) -> None:
        tracker = PhaseTracker()
        for time_s, phase in ((0.0, 0.0), (0.01, 0.1), (0.02, 0.2)):
            sample = tracker.update(time_s, *self.vector_for_phase(phase))
        tracker.update(0.03, None, None)
        tracker.update(0.04, None, None)
        before_reacquisition = tracker.phase_tracked_rad

        recovered = tracker.update(0.05, *self.vector_for_phase(0.4))

        self.assertEqual(recovered.status, PhaseConfidence.MEASURED)
        self.assertLess(abs(recovered.phase_tracked_rad - before_reacquisition), 0.2)
        self.assertLess(abs(recovered.phase_error_rad), 0.1)

    def test_phase_correction_is_capped_per_update(self) -> None:
        tracker = PhaseTracker(
            max_phase_rate_rad_s=100.0,
            omega_filter_tau_s=1.0,
            phase_correction_gain=1.0,
            max_phase_correction_rad=0.1,
        )
        tracker.update(0.0, *self.vector_for_phase(0.0))
        tracker.update(0.01, *self.vector_for_phase(0.01))
        tracker.update(0.02, *self.vector_for_phase(0.02))
        before_correction = tracker.phase_tracked_rad
        sample = tracker.update(0.03, *self.vector_for_phase(0.5))

        predicted = before_correction + sample.omega_2x_rad_s * 0.01
        self.assertLessEqual(abs(sample.phase_tracked_rad - predicted), 0.1)


class PhaseTrackedSineTargetTests(unittest.TestCase):
    def test_requested_amplitude_bounds_target_and_phase_zero_selects_quadrature(self) -> None:
        target = PhaseTrackedSineTarget(
            amplitude_mm=5.0,
            phase_offset_deg=0.0,
            start_position_mm=0.0,
            start_time_s=0.0,
            ramp_duration_ms=1.0,
        )
        for phase_rad in (0.0, math.pi / 2.0, math.pi, 3.0 * math.pi / 2.0):
            normalized, command_mm = target.target(1.0, phase_rad)
            self.assertAlmostEqual(normalized, math.sin(phase_rad))
            self.assertAlmostEqual(command_mm, 5.0 * math.sin(phase_rad))
            self.assertLessEqual(abs(command_mm), 5.0)

    def test_startup_ramp_begins_at_actual_position_and_stays_within_amplitude(self) -> None:
        target = PhaseTrackedSineTarget(
            amplitude_mm=5.0,
            phase_offset_deg=0.0,
            start_position_mm=2.0,
            start_time_s=1.0,
            ramp_duration_ms=100.0,
        )

        normalized, initial = target.target(1.0, math.pi / 2.0)
        _, halfway = target.target(1.05, math.pi / 2.0)
        _, complete = target.target(1.1, math.pi / 2.0)

        self.assertAlmostEqual(initial, 2.0)
        self.assertAlmostEqual(halfway, 3.5)
        self.assertAlmostEqual(complete, 5.0)
        self.assertAlmostEqual(normalized, 1.0)
        self.assertTrue(all(abs(value) <= 5.0 for value in (initial, halfway, complete)))

    def test_startup_rejects_position_outside_requested_amplitude(self) -> None:
        with self.assertRaisesRegex(ValueError, "within requested amplitude"):
            PhaseTrackedSineTarget(
                amplitude_mm=5.0,
                phase_offset_deg=0.0,
                start_position_mm=6.0,
                start_time_s=0.0,
            )


if __name__ == "__main__":
    unittest.main()
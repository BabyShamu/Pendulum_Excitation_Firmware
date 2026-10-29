"""Target detection regressions based on 6-mm / 1.79-Hz experiments."""
import unittest

from pendulum_workbench.experiments.target_crossing import TargetCrossingDetector


class TargetCrossingDetectorTests(unittest.TestCase):
    def test_first_recorded_6mm_peak(self):
        detector = TargetCrossingDetector(70.0)
        angles = [68.82, 69.79, 70.05, 69.61, 68.55]
        observed = [detector.observe(0.02 * i, angle) for i, angle in enumerate(angles)]
        self.assertEqual([x for x in observed if x is not None], [0.04])

    def test_second_recorded_6mm_peak(self):
        detector = TargetCrossingDetector(70.0)
        angles = [68.55, 69.87, 70.14, 69.70, 68.47]
        observed = [detector.observe(0.02 * i, angle) for i, angle in enumerate(angles)]
        self.assertEqual([x for x in observed if x is not None], [0.04])

    def test_isolated_large_spike_is_rejected(self):
        detector = TargetCrossingDetector(70.0)
        angles = [21.0, 22.0, 74.0, 22.5, 23.0]
        self.assertTrue(all(detector.observe(0.02 * i, a) is None
                            for i, a in enumerate(angles)))

    def test_single_sample_above_target_with_smooth_neighbors_is_accepted(self):
        detector = TargetCrossingDetector(70.0)
        self.assertIsNone(detector.observe(1.00, 69.7))
        self.assertIsNone(detector.observe(1.02, 70.08))
        self.assertAlmostEqual(detector.observe(1.04, 69.5), 1.02)

    def test_completed_run_peak_is_recorded_as_target_crossing(self):
        detector = TargetCrossingDetector(50.0)
        observed = [
            detector.observe(12.055, 49.4),
            detector.observe(12.075, 51.15),
            detector.observe(12.095, 50.8),
        ]
        self.assertEqual([value for value in observed if value is not None], [12.075])

    def test_firmware_reported_80_degree_peak_is_validated_from_telemetry(self):
        detector = TargetCrossingDetector(80.0)
        observed = [
            detector.observe(20.00, 80.07),
            detector.observe(20.02, 80.42),
            detector.observe(20.04, 79.72),
        ]
        self.assertEqual([value for value in observed if value is not None], [20.02])

    def test_sustained_crossing_is_validated(self):
        detector = TargetCrossingDetector(80.0)
        observed = [
            detector.observe(20.00, 79.6),
            detector.observe(20.02, 80.1),
            detector.observe(20.04, 80.4),
        ]
        self.assertEqual([value for value in observed if value is not None], [20.02])

    def test_negative_angle_peak_is_validated(self):
        detector = TargetCrossingDetector(80.0)
        observed = [
            detector.observe(20.00, -80.07),
            detector.observe(20.02, -80.42),
            detector.observe(20.04, -79.72),
        ]
        self.assertEqual([value for value in observed if value is not None], [20.02])

    def test_missing_sample_breaks_peak_continuity(self):
        detector = TargetCrossingDetector(80.0)
        self.assertIsNone(detector.observe(20.00, 80.07))
        self.assertIsNone(detector.observe(20.02, 80.42))
        self.assertIsNone(detector.observe(20.40, 79.72))

    def test_small_threshold_noise_is_validated_but_large_spike_is_rejected(self):
        detector = TargetCrossingDetector(80.0)
        noisy = [
            detector.observe(20.00, 79.8),
            detector.observe(20.02, 80.1),
            detector.observe(20.04, 79.7),
        ]
        self.assertEqual([value for value in noisy if value is not None], [20.02])

        detector.reset()
        spike = [
            detector.observe(21.00, 40.0),
            detector.observe(21.02, 80.42),
            detector.observe(21.04, 40.5),
        ]
        self.assertTrue(all(value is None for value in spike))

    def test_crossing_at_stopping_boundary_is_not_lost(self):
        detector = TargetCrossingDetector(50.0)
        self.assertIsNone(detector.observe(12.055, 49.4))
        self.assertIsNone(detector.observe(12.075, 51.15))
        self.assertEqual(detector.observe(12.095, 50.8), 12.075)

    def test_gap_cannot_validate_old_crossing(self):
        detector = TargetCrossingDetector(70.0)
        self.assertIsNone(detector.observe(1.0, 69.9))
        self.assertIsNone(detector.observe(1.02, 70.1))
        self.assertIsNone(detector.observe(1.52, 69.9))

    def test_invalid_sample_breaks_confirmation(self):
        detector = TargetCrossingDetector(70.0)
        detector.observe(1.0, 69.9)
        detector.observe(1.02, 70.1)
        self.assertIsNone(detector.observe(1.04, float("nan")))
        self.assertIsNone(detector.observe(1.06, 69.9))

    def test_target_not_reached(self):
        detector = TargetCrossingDetector(70.0)
        self.assertTrue(all(detector.observe(0.02*i, a) is None
                            for i, a in enumerate([66.1, 68.8, 69.99, 69.8, 65.1])))


if __name__ == "__main__":
    unittest.main()

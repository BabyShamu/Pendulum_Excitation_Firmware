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

import csv
import math
import tempfile
import unittest
from pathlib import Path

from pendulum_workbench.domain.angle_prediction import AngleDropoutPredictor, AngleSource
from pendulum_workbench.experiments.angle_prediction_validation import (
    DropoutWindow,
    MeasuredAngleSample,
    evaluate_dropout_sweep,
    load_telemetry_csv,
    simulate_dropout_window,
    write_sweep_csv,
)


class AngleDropoutPredictorTests(unittest.TestCase):
    def test_velocity_uses_only_valid_measured_samples(self) -> None:
        predictor = AngleDropoutPredictor(max_prediction_age_s=1.0)
        predictor.observe(0.0, 10.0)
        predictor.observe(0.1, 11.0)
        measured_velocity = predictor.omega_est_deg_s

        estimate = predictor.observe(0.2, None)
        predictor.observe(0.3, None)

        self.assertAlmostEqual(measured_velocity, 10.0)
        self.assertEqual(predictor.omega_est_deg_s, measured_velocity)
        self.assertEqual(estimate.angle_source, AngleSource.PREDICTED)

    def test_prediction_uses_degrees_and_first_order_extrapolation(self) -> None:
        predictor = AngleDropoutPredictor(max_prediction_age_s=0.5)
        predictor.observe(0.0, 30.0)
        predictor.observe(0.1, 32.0)

        estimate = predictor.observe(0.15, None)

        self.assertEqual(estimate.angle_source, AngleSource.PREDICTED)
        self.assertAlmostEqual(predictor.omega_est_deg_s, 20.0)
        self.assertAlmostEqual(estimate.predicted_angle_deg, 33.0)
        self.assertAlmostEqual(estimate.prediction_age_ms, 50.0)

    def test_angle_wraparound_is_unwrapped_for_rate_and_wrapped_for_prediction(self) -> None:
        predictor = AngleDropoutPredictor(max_prediction_age_s=0.5)
        predictor.observe(0.0, 179.0)
        predictor.observe(0.1, -179.0)

        estimate = predictor.observe(0.2, None)

        self.assertAlmostEqual(predictor.omega_est_deg_s, 20.0)
        self.assertAlmostEqual(estimate.predicted_angle_deg, -177.0)

    def test_dropout_preserves_last_measurement_and_recovery_restores_authority(self) -> None:
        predictor = AngleDropoutPredictor(max_prediction_age_s=1.0)
        predictor.observe(0.0, 10.0)
        predictor.observe(0.1, 11.0)
        predictor.observe(0.2, None)
        predicted = predictor.observe(0.25, None)

        self.assertEqual(predicted.angle_source, AngleSource.PREDICTED)
        self.assertAlmostEqual(predicted.predicted_angle_deg, 12.5)
        self.assertEqual(predictor.last_measured_angle_deg, 11.0)

        recovered = predictor.observe(0.3, 13.2)

        self.assertEqual(recovered.angle_source, AngleSource.MEASURED)
        self.assertAlmostEqual(recovered.predicted_angle_deg, 13.0)
        self.assertAlmostEqual(recovered.prediction_recovery_error_deg, 0.2)
        self.assertAlmostEqual(recovered.outage_duration_ms, 100.0)
        self.assertEqual(predictor.last_measured_angle_deg, 13.2)

    def test_prediction_is_unavailable_without_measured_velocity_or_after_run_horizon(self) -> None:
        predictor = AngleDropoutPredictor(max_prediction_age_s=0.2)
        predictor.observe(0.0, 5.0)
        self.assertEqual(predictor.observe(0.1, None).angle_source, AngleSource.UNAVAILABLE)
        predictor.observe(0.1, 6.0)
        self.assertEqual(predictor.observe(0.31, None).angle_source, AngleSource.UNAVAILABLE)


class OfflineAnglePredictionValidationTests(unittest.TestCase):
    @staticmethod
    def linear_samples() -> list[MeasuredAngleSample]:
        return [MeasuredAngleSample(index * 0.02, 5.0 + 3.0 * index * 0.02) for index in range(101)]

    def test_masked_ground_truth_does_not_leak_into_prediction(self) -> None:
        samples = [MeasuredAngleSample(index * 0.1, 10.0 + 2.0 * index * 0.1) for index in range(6)]
        window = DropoutWindow(start_s=0.2, duration_ms=200.0)
        baseline = simulate_dropout_window(samples, window)
        changed_hidden_truth = [
            MeasuredAngleSample(
                sample.time_s,
                sample.angle_deg + 50.0 if 0.2 <= sample.time_s < 0.4 else sample.angle_deg,
            )
            for sample in samples
        ]

        changed = simulate_dropout_window(changed_hidden_truth, window)

        self.assertEqual(
            [item.predicted_angle_deg for item in baseline.predictions],
            [item.predicted_angle_deg for item in changed.predictions],
        )
        self.assertNotEqual(
            [item.absolute_error_deg for item in baseline.predictions],
            [item.absolute_error_deg for item in changed.predictions],
        )
        self.assertTrue(all(item.absolute_error_deg < 1e-9 for item in baseline.predictions))

    def test_dropout_sweep_is_deterministic_and_writes_requested_metrics(self) -> None:
        samples = self.linear_samples()
        durations_ms = (40.0, 100.0, 200.0)

        first = evaluate_dropout_sweep(samples, durations_ms, windows_per_duration=3)
        second = evaluate_dropout_sweep(samples, durations_ms, windows_per_duration=3)

        self.assertEqual(first, second)
        self.assertEqual([row.dropout_duration_ms for row in first], list(durations_ms))
        self.assertTrue(all(row.number_of_windows == 3 for row in first))
        self.assertTrue(all(row.mean_abs_error_deg is not None for row in first))
        self.assertTrue(all(math.isfinite(row.rmse_deg) for row in first))

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "derived" / "angle_dropout_sweep.csv"
            write_sweep_csv(output, first)
            with output.open("r", encoding="utf-8", newline="") as result_file:
                rows = list(csv.DictReader(result_file))
            self.assertEqual(
                list(rows[0]),
                [
                    "dropout_duration_ms",
                    "mean_abs_error_deg",
                    "rmse_deg",
                    "max_abs_error_deg",
                    "number_of_windows",
                ],
            )
            self.assertEqual(len(rows), len(durations_ms))

    def test_loader_reads_existing_telemetry_csv_without_modifying_it(self) -> None:
        content = (
            "sample_index,time_s,angle_deg,position_mm\n"
            "1,0.0,10.0,0.0\n"
            "2,0.02,NaN,0.0\n"
            "3,0.04,10.2,0.0\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            telemetry_path = Path(directory) / "telemetry.csv"
            telemetry_path.write_text(content, encoding="utf-8")

            samples = load_telemetry_csv(telemetry_path)

            self.assertEqual(samples[0].angle_deg, 10.0)
            self.assertIsNone(samples[1].angle_deg)
            self.assertEqual(samples[2].angle_deg, 10.2)
            self.assertEqual(telemetry_path.read_text(encoding="utf-8"), content)


if __name__ == "__main__":
    unittest.main()
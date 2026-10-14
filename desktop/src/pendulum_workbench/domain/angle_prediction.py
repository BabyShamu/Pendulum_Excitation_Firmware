import math
from collections import deque
from dataclasses import dataclass
from enum import Enum


class AngleSource(str, Enum):
    MEASURED = "measured"
    PREDICTED = "predicted"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class AnglePrediction:
    angle_source: AngleSource
    predicted_angle_deg: float | None = None
    prediction_age_ms: float | None = None
    omega_est_deg_s: float | None = None
    prediction_recovery_error_deg: float | None = None
    outage_duration_ms: float | None = None


class AngleDropoutPredictor:
    """First-order, measured-sample-only predictor for validation and analysis."""

    def __init__(self, *, max_prediction_age_s: float, recent_sample_count: int = 5) -> None:
        if not math.isfinite(max_prediction_age_s) or max_prediction_age_s <= 0.0:
            raise ValueError("prediction horizon must be finite and greater than zero")
        if recent_sample_count < 2:
            raise ValueError("at least two recent measured samples are required")
        self.max_prediction_age_s = max_prediction_age_s
        self._samples: deque[tuple[float, float, float]] = deque(maxlen=recent_sample_count)
        self._omega_est_deg_s: float | None = None
        self._outage_started_s: float | None = None
        self._last_observation_s: float | None = None

    @property
    def omega_est_deg_s(self) -> float | None:
        return self._omega_est_deg_s

    @property
    def last_measured_angle_deg(self) -> float | None:
        return self._samples[-1][1] if self._samples else None

    @property
    def outage_started_s(self) -> float | None:
        return self._outage_started_s

    def reset(self) -> None:
        self._samples.clear()
        self._omega_est_deg_s = None
        self._outage_started_s = None
        self._last_observation_s = None

    def observe(self, time_s: float, measured_angle_deg: float | None) -> AnglePrediction:
        if not math.isfinite(time_s):
            raise ValueError("sample time must be finite")
        if self._last_observation_s is not None and time_s < self._last_observation_s:
            raise ValueError("sample times must be nondecreasing")
        self._last_observation_s = time_s

        if measured_angle_deg is None or not math.isfinite(measured_angle_deg):
            if self._outage_started_s is None:
                self._outage_started_s = time_s
            return self._predict(time_s)

        if self._samples and time_s == self._samples[-1][0]:
            return AnglePrediction(
                angle_source=AngleSource.MEASURED,
                omega_est_deg_s=self._omega_est_deg_s,
            )

        predicted_at_recovery = self._predict(time_s) if self._outage_started_s is not None else None
        outage_duration_ms = (
            (time_s - self._outage_started_s) * 1000.0
            if self._outage_started_s is not None
            else None
        )
        recovery_error = None
        predicted_angle = None
        prediction_age_ms = None
        if predicted_at_recovery is not None:
            predicted_angle = predicted_at_recovery.predicted_angle_deg
            prediction_age_ms = predicted_at_recovery.prediction_age_ms
            if predicted_at_recovery.angle_source == AngleSource.PREDICTED:
                recovery_error = self._wrapped_delta_deg(measured_angle_deg, predicted_angle)

        unwrapped_angle = measured_angle_deg
        if self._samples:
            previous_time, previous_angle, previous_unwrapped = self._samples[-1]
            if time_s <= previous_time:
                return AnglePrediction(
                    angle_source=AngleSource.MEASURED,
                    omega_est_deg_s=self._omega_est_deg_s,
                    prediction_recovery_error_deg=recovery_error,
                    outage_duration_ms=outage_duration_ms,
                )
            unwrapped_angle = previous_unwrapped + self._wrapped_delta_deg(
                measured_angle_deg,
                previous_angle,
            )

        self._samples.append((time_s, measured_angle_deg, unwrapped_angle))
        self._estimate_velocity()
        self._outage_started_s = None
        return AnglePrediction(
            angle_source=AngleSource.MEASURED,
            predicted_angle_deg=predicted_angle,
            prediction_age_ms=prediction_age_ms,
            omega_est_deg_s=self._omega_est_deg_s,
            prediction_recovery_error_deg=recovery_error,
            outage_duration_ms=outage_duration_ms,
        )

    def _predict(self, time_s: float) -> AnglePrediction:
        if not self._samples:
            return AnglePrediction(angle_source=AngleSource.UNAVAILABLE)
        age_s = time_s - self._samples[-1][0]
        age_ms = max(0.0, age_s * 1000.0)
        if (
            age_s < 0.0
            or age_s > self.max_prediction_age_s
            or self._omega_est_deg_s is None
        ):
            return AnglePrediction(
                angle_source=AngleSource.UNAVAILABLE,
                prediction_age_ms=age_ms,
                omega_est_deg_s=self._omega_est_deg_s,
            )
        unwrapped_angle = self._samples[-1][2] + self._omega_est_deg_s * age_s
        return AnglePrediction(
            angle_source=AngleSource.PREDICTED,
            predicted_angle_deg=self._wrap_angle_deg(unwrapped_angle),
            prediction_age_ms=age_ms,
            omega_est_deg_s=self._omega_est_deg_s,
        )

    def _estimate_velocity(self) -> None:
        if len(self._samples) < 2:
            self._omega_est_deg_s = None
            return
        mean_time = sum(sample[0] for sample in self._samples) / len(self._samples)
        mean_angle = sum(sample[2] for sample in self._samples) / len(self._samples)
        time_variance = sum((sample[0] - mean_time) ** 2 for sample in self._samples)
        if time_variance <= 0.0:
            self._omega_est_deg_s = None
            return
        covariance = sum(
            (sample[0] - mean_time) * (sample[2] - mean_angle)
            for sample in self._samples
        )
        self._omega_est_deg_s = covariance / time_variance

    @staticmethod
    def _wrapped_delta_deg(angle_deg: float, reference_deg: float) -> float:
        return (angle_deg - reference_deg + 180.0) % 360.0 - 180.0

    @staticmethod
    def _wrap_angle_deg(angle_deg: float) -> float:
        return (angle_deg + 180.0) % 360.0 - 180.0
import math
from dataclasses import dataclass
from enum import Enum


# Initial commissioning values only; they have not been validated on hardware.
PHASE_TRACKED_MIN_VECTOR_MAGNITUDE = 0.05
PHASE_TRACKED_MAX_PHASE_RATE_RAD_S = 40.0
PHASE_TRACKED_OMEGA_FILTER_TAU_S = 0.05
PHASE_TRACKED_PHASE_CORRECTION_GAIN = 0.15
PHASE_TRACKED_MAX_PHASE_CORRECTION_RAD = 0.1
PHASE_TRACKED_MAX_UNCONFIDENT_MS = 250.0
PHASE_TRACKED_START_RAMP_MS = 250.0


class PhaseConfidence(str, Enum):
    ACQUIRING = "acquiring"
    MEASURED = "measured"
    PROPAGATED = "propagated"
    LOW = "low"
    FAULT = "fault"


@dataclass(frozen=True, slots=True)
class PhaseTrackerSample:
    status: PhaseConfidence
    phase_raw_rad: float | None
    phase_tracked_rad: float | None
    omega_2x_rad_s: float | None
    phase_magnitude: float
    phase_error_rad: float | None = None


@dataclass(frozen=True, slots=True)
class PhaseTrackedTarget:
    status: PhaseConfidence
    phase: PhaseTrackerSample
    normalized_sine: float
    commanded_target_mm: float


class PhaseTracker:
    """Small unwrap/filter/correct tracker for direct/quadrature 2x phase."""

    def __init__(
        self,
        *,
        min_vector_magnitude: float = PHASE_TRACKED_MIN_VECTOR_MAGNITUDE,
        max_phase_rate_rad_s: float = PHASE_TRACKED_MAX_PHASE_RATE_RAD_S,
        omega_filter_tau_s: float = PHASE_TRACKED_OMEGA_FILTER_TAU_S,
        phase_correction_gain: float = PHASE_TRACKED_PHASE_CORRECTION_GAIN,
        max_phase_correction_rad: float = PHASE_TRACKED_MAX_PHASE_CORRECTION_RAD,
        max_unconfident_ms: float = PHASE_TRACKED_MAX_UNCONFIDENT_MS,
    ) -> None:
        values = (
            min_vector_magnitude,
            max_phase_rate_rad_s,
            omega_filter_tau_s,
            phase_correction_gain,
            max_phase_correction_rad,
            max_unconfident_ms,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("phase-tracker settings must be finite")
        if min_vector_magnitude <= 0.0 or max_phase_rate_rad_s <= 0.0:
            raise ValueError("phase confidence and phase-rate bounds must be positive")
        if (
            omega_filter_tau_s <= 0.0
            or not 0.0 <= phase_correction_gain <= 1.0
            or max_phase_correction_rad <= 0.0
        ):
            raise ValueError("phase filter/correction settings are invalid")
        if max_unconfident_ms <= 0.0:
            raise ValueError("unconfident timeout must be positive")

        self.min_vector_magnitude = min_vector_magnitude
        self.max_phase_rate_rad_s = max_phase_rate_rad_s
        self.omega_filter_tau_s = omega_filter_tau_s
        self.phase_correction_gain = phase_correction_gain
        self.max_phase_correction_rad = max_phase_correction_rad
        self.max_unconfident_ms = max_unconfident_ms
        self.reset()

    def reset(self, time_s: float | None = None) -> None:
        self.phase_initialized = False
        self.previous_phase_raw_rad: float | None = None
        self.measured_phase_unwrapped_rad: float | None = None
        self.phase_tracked_rad: float | None = None
        self.omega_2x_rad_s: float | None = None
        self.previous_measurement_time_s: float | None = None
        self.previous_update_time_s = time_s
        self.unconfident_since_s: float | None = None
        self.phase_magnitude = 0.0

    def update(
        self,
        time_s: float,
        direct: float | None,
        quadrature: float | None,
    ) -> PhaseTrackerSample:
        if not math.isfinite(time_s):
            raise ValueError("phase sample time must be finite")
        if (
            self.previous_update_time_s is not None
            and time_s <= self.previous_update_time_s
        ):
            raise ValueError("phase sample times must increase")

        dt = (
            0.0
            if self.previous_update_time_s is None
            else time_s - self.previous_update_time_s
        )
        self.previous_update_time_s = time_s

        if direct is None or quadrature is None:
            return self._propagate_unconfident(time_s, dt, None, 0.0)
        if not math.isfinite(direct) or not math.isfinite(quadrature):
            return self._propagate_unconfident(time_s, dt, None, 0.0)

        magnitude = math.hypot(direct, quadrature)
        self.phase_magnitude = magnitude
        if magnitude < self.min_vector_magnitude:
            return self._propagate_unconfident(time_s, dt, None, magnitude)

        # Existing convention: direct ~= -cos(phase2), quadrature ~= sin(phase2).
        phase_raw = math.atan2(quadrature, -direct)
        if not self.phase_initialized:
            self.phase_initialized = True
            self.previous_phase_raw_rad = phase_raw
            self.measured_phase_unwrapped_rad = phase_raw
            self.phase_tracked_rad = phase_raw
            self.previous_measurement_time_s = time_s
            self.unconfident_since_s = None
            return self._result(PhaseConfidence.MEASURED, phase_raw, magnitude)

        previous_raw = self.previous_phase_raw_rad
        previous_measurement_time = self.previous_measurement_time_s
        if previous_raw is None or previous_measurement_time is None:
            return self._propagate_unconfident(time_s, dt, phase_raw, magnitude)
        measurement_dt = time_s - previous_measurement_time
        if measurement_dt <= 0.0:
            return self._propagate_unconfident(time_s, dt, phase_raw, magnitude)
        phase_increment = self.wrap_to_pi(phase_raw - previous_raw)
        measured_omega = phase_increment / measurement_dt
        if abs(measured_omega) > self.max_phase_rate_rad_s:
            return self._propagate_unconfident(time_s, dt, phase_raw, magnitude)

        if self.omega_2x_rad_s is None:
            self.omega_2x_rad_s = measured_omega
        else:
            alpha = measurement_dt / (measurement_dt + self.omega_filter_tau_s)
            self.omega_2x_rad_s += alpha * (measured_omega - self.omega_2x_rad_s)

        if self.phase_tracked_rad is None or self.measured_phase_unwrapped_rad is None:
            self.phase_tracked_rad = phase_raw
            self.measured_phase_unwrapped_rad = phase_raw
        else:
            self.measured_phase_unwrapped_rad += phase_increment
            self.phase_tracked_rad += (self.omega_2x_rad_s or 0.0) * dt
            phase_error = self.wrap_to_pi(
                self.measured_phase_unwrapped_rad - self.phase_tracked_rad
            )
            correction = self.phase_correction_gain * phase_error
            correction = min(
                self.max_phase_correction_rad,
                max(-self.max_phase_correction_rad, correction),
            )
            self.phase_tracked_rad += correction

        self.previous_phase_raw_rad = phase_raw
        self.previous_measurement_time_s = time_s
        self.unconfident_since_s = None
        return self._result(PhaseConfidence.MEASURED, phase_raw, magnitude)

    def _propagate_unconfident(
        self,
        time_s: float,
        dt: float,
        phase_raw: float | None,
        magnitude: float,
    ) -> PhaseTrackerSample:
        if self.unconfident_since_s is None:
            self.unconfident_since_s = time_s
        if self.phase_tracked_rad is not None and self.omega_2x_rad_s is not None:
            self.phase_tracked_rad += self.omega_2x_rad_s * dt
        age_ms = (time_s - self.unconfident_since_s) * 1000.0
        status = (
            PhaseConfidence.FAULT
            if age_ms > self.max_unconfident_ms
            else PhaseConfidence.PROPAGATED
            if self.phase_tracked_rad is not None and self.omega_2x_rad_s is not None
            else PhaseConfidence.ACQUIRING
        )
        self.phase_magnitude = magnitude
        return self._result(status, phase_raw, magnitude)

    def _result(
        self,
        status: PhaseConfidence,
        phase_raw_rad: float | None,
        magnitude: float,
    ) -> PhaseTrackerSample:
        phase_error = None
        if self.phase_tracked_rad is not None and self.measured_phase_unwrapped_rad is not None:
            phase_error = self.wrap_to_pi(
                self.measured_phase_unwrapped_rad - self.phase_tracked_rad
            )
        return PhaseTrackerSample(
            status=status,
            phase_raw_rad=phase_raw_rad,
            phase_tracked_rad=self.phase_tracked_rad,
            omega_2x_rad_s=self.omega_2x_rad_s,
            phase_magnitude=magnitude,
            phase_error_rad=phase_error,
        )

    @staticmethod
    def wrap_to_pi(angle_rad: float) -> float:
        return (angle_rad + math.pi) % (2.0 * math.pi) - math.pi


class PhaseTrackedSineTarget:
    def __init__(
        self,
        *,
        amplitude_mm: float,
        phase_offset_deg: float,
        start_position_mm: float,
        start_time_s: float,
        ramp_duration_ms: float = PHASE_TRACKED_START_RAMP_MS,
    ) -> None:
        if not all(
            math.isfinite(value)
            for value in (amplitude_mm, phase_offset_deg, start_position_mm, start_time_s, ramp_duration_ms)
        ):
            raise ValueError("phase-tracked sine parameters must be finite")
        if amplitude_mm <= 0.0 or ramp_duration_ms <= 0.0:
            raise ValueError("amplitude and startup ramp must be positive")
        if abs(start_position_mm) > amplitude_mm:
            raise ValueError("current pivot position must be within requested amplitude for smooth startup")
        self.amplitude_mm = amplitude_mm
        self.phase_offset_rad = math.radians(phase_offset_deg)
        self.start_position_mm = start_position_mm
        self.start_time_s = start_time_s
        self.ramp_duration_s = ramp_duration_ms / 1000.0

    def target(self, time_s: float, tracked_phase_rad: float | None) -> tuple[float, float]:
        if not math.isfinite(time_s):
            raise ValueError("target time must be finite")
        if tracked_phase_rad is None:
            return 0.0, self.start_position_mm
        normalized_sine = math.sin(tracked_phase_rad - self.phase_offset_rad)
        ramp = min(1.0, max(0.0, (time_s - self.start_time_s) / self.ramp_duration_s))
        ramped_target = (
            (1.0 - ramp) * self.start_position_mm
            + ramp * self.amplitude_mm * normalized_sine
        )
        return normalized_sine, ramped_target
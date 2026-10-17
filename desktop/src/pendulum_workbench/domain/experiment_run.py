import math
import re
from dataclasses import dataclass
from enum import Enum

from pendulum_workbench.domain.angle_prediction import AngleSource


class ExperimentMode(str, Enum):
    VERTICAL_SINUSOIDAL_EXCITATION = "Vertical Sinusoidal Excitation"
    AUTO_RESONANCE_PARAMETRIC = "Auto-Resonance"
    AUTO_RESONANCE_PARAMETRIC_CLOSED_LOOP = "Auto-Resonance"
    PHASE_TRACKED_SINE_AUTO_RESONANCE = "Phase-Tracked Sinusoidal Auto-Resonance"


class ExperimentRunState(str, Enum):
    READY = "READY"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    COMPLETE = "COMPLETE"
    OPERATOR_STOPPED = "OPERATOR STOPPED"
    INTERRUPTED = "INTERRUPTED"
    FAULT = "FAULT"


@dataclass(frozen=True, slots=True)
class ExperimentRunSnapshot:
    state: ExperimentRunState = ExperimentRunState.READY
    definition: "ExperimentDefinition | None" = None
    experiment_id: str | None = None
    generated_name: str = ""
    directory: str | None = None
    measured_initial_angle_deg: float | None = None
    started_at: str | None = None
    elapsed_s: float = 0.0
    actual_elapsed_duration_s: float | None = None
    sample_count: int = 0
    angle_source: AngleSource = AngleSource.UNAVAILABLE
    predicted_angle_deg: float | None = None
    prediction_age_ms: float | None = None
    omega_est_deg_s: float | None = None
    prediction_recovery_error_deg: float | None = None
    prediction_outage_duration_ms: float | None = None
    recording_state: str = "Off"
    completion_reason: str = ""
    error_message: str = ""


@dataclass(frozen=True, slots=True)
class VerticalSineDefinition:
    amplitude_mm: float
    frequency_hz: float
    duration_s: float
    label: str = ""
    notes: str = ""
    target_angle_deg: float = 85.0
    mode: ExperimentMode = ExperimentMode.VERTICAL_SINUSOIDAL_EXCITATION

    def validate(self) -> None:
        if not math.isfinite(self.target_angle_deg) or not 0.0 < self.target_angle_deg <= 85.0:
            raise ValueError("target angle must be greater than 0 and at most 85 degrees")
        if self.mode != ExperimentMode.VERTICAL_SINUSOIDAL_EXCITATION:
            raise ValueError("VerticalSineDefinition requires Vertical Sinusoidal Excitation mode")
        values = (
            ("pivot amplitude", self.amplitude_mm),
            ("excitation frequency", self.frequency_hz),
            ("duration", self.duration_s),
        )
        for name, value in values:
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if name == "pivot amplitude" and value < 0.0:
                raise ValueError("pivot amplitude must be non-negative")
            if name != "pivot amplitude" and value <= 0.0:
                raise ValueError(f"{name} must be greater than zero")

    def partial_name(self) -> str:
        return self._label_prefix() + (
            f"VS_A{self._number(self.amplitude_mm)}mm_"
            f"F{self._number(self.frequency_hz)}Hz_T{self._number(self.duration_s)}s"
        )

    def final_name(self, measured_initial_angle_deg: float) -> str:
        if not math.isfinite(measured_initial_angle_deg):
            raise ValueError("measured initial angle must be finite")
        if measured_initial_angle_deg < 0.0:
            angle_token = f"m{self._number(abs(measured_initial_angle_deg))}"
        elif measured_initial_angle_deg > 0.0:
            angle_token = f"p{self._number(measured_initial_angle_deg)}"
        else:
            angle_token = "0"
        return self._label_prefix() + (
            f"VS_A{self._number(self.amplitude_mm)}mm_"
            f"F{self._number(self.frequency_hz)}Hz_"
            f"Theta{angle_token}deg_T{self._number(self.duration_s)}s"
        )

    def manifest_values(
        self,
        *,
        measured_initial_angle_deg: float | None,
        actual_elapsed_duration_s: float | None = None,
        completion_reason: str | None = None,
    ) -> dict[str, object]:
        return {
            "experiment_mode": self.mode.name,
            "commanded_amplitude_mm": self.amplitude_mm,
            "commanded_frequency_hz": self.frequency_hz,
            "requested_duration_s": self.duration_s,
            "target_angle_deg": self.target_angle_deg,
            "actual_elapsed_duration_s": actual_elapsed_duration_s,
            "measured_initial_angle_deg": measured_initial_angle_deg,
            "optional_label": self.label.strip(),
            "notes": self.notes.strip(),
            "generated_run_name": (
                self.partial_name()
                if measured_initial_angle_deg is None
                else self.final_name(measured_initial_angle_deg)
            ),
            "completion_reason": completion_reason,
        }

    def _label_prefix(self) -> str:
        label = re.sub(r"[^A-Za-z0-9._-]+", "_", self.label.strip()).strip("._-")
        return f"{label}_" if label else ""

    @staticmethod
    def _number(value: float) -> str:
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"


@dataclass(frozen=True, slots=True)
class AutoResonanceDefinition:
    amplitude_mm: float
    phase_deg: float
    duration_s: float
    label: str = ""
    notes: str = ""
    target_angle_deg: float = 85.0
    mode: ExperimentMode = ExperimentMode.AUTO_RESONANCE_PARAMETRIC

    def validate(self) -> None:
        if not math.isfinite(self.target_angle_deg) or not 0.0 < self.target_angle_deg <= 85.0:
            raise ValueError("target angle must be greater than 0 and at most 85 degrees")
        if self.mode != ExperimentMode.AUTO_RESONANCE_PARAMETRIC:
            raise ValueError("AutoResonanceDefinition requires Auto-Resonance mode")
        for name, value in (
            ("pivot amplitude", self.amplitude_mm),
            ("phase", self.phase_deg),
            ("duration", self.duration_s),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.amplitude_mm <= 0.0:
            raise ValueError("pivot amplitude must be greater than zero")
        if self.duration_s <= 0.0:
            raise ValueError("duration must be greater than zero")

    def partial_name(self) -> str:
        return self._label_prefix() + (
            f"AR_A{self._number(self.amplitude_mm)}mm_"
            f"Phi{self._phase_token()}deg_T{self._number(self.duration_s)}s"
        )

    def final_name(self, measured_initial_angle_deg: float) -> str:
        if not math.isfinite(measured_initial_angle_deg):
            raise ValueError("measured initial angle must be finite")
        if measured_initial_angle_deg < 0.0:
            angle_token = f"m{self._number(abs(measured_initial_angle_deg))}"
        elif measured_initial_angle_deg > 0.0:
            angle_token = f"p{self._number(measured_initial_angle_deg)}"
        else:
            angle_token = "0"
        return self._label_prefix() + (
            f"AR_A{self._number(self.amplitude_mm)}mm_"
            f"Phi{self._phase_token()}deg_"
            f"Theta{angle_token}deg_T{self._number(self.duration_s)}s"
        )

    def manifest_values(
        self,
        *,
        measured_initial_angle_deg: float | None,
        actual_elapsed_duration_s: float | None = None,
        completion_reason: str | None = None,
    ) -> dict[str, object]:
        return {
            "experiment_mode": self.mode.name,
            "commanded_amplitude_mm": self.amplitude_mm,
            "commanded_phase_deg": self.phase_deg,
            "requested_duration_s": self.duration_s,
            "target_angle_deg": self.target_angle_deg,
            "actual_elapsed_duration_s": actual_elapsed_duration_s,
            "measured_initial_angle_deg": measured_initial_angle_deg,
            "optional_label": self.label.strip(),
            "notes": self.notes.strip(),
            "generated_run_name": (
                self.partial_name()
                if measured_initial_angle_deg is None
                else self.final_name(measured_initial_angle_deg)
            ),
            "completion_reason": completion_reason,
        }

    def _label_prefix(self) -> str:
        label = re.sub(r"[^A-Za-z0-9._-]+", "_", self.label.strip()).strip("._-")
        return f"{label}_" if label else ""

    def _phase_token(self) -> str:
        if self.phase_deg < 0.0:
            return f"m{self._number(abs(self.phase_deg))}"
        if self.phase_deg > 0.0:
            return f"p{self._number(self.phase_deg)}"
        return "0"

    @staticmethod
    def _number(value: float) -> str:
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"


@dataclass(frozen=True, slots=True)
class PhaseTrackedSineDefinition:
    amplitude_mm: float
    phase_deg: float
    duration_s: float
    label: str = ""
    notes: str = ""
    target_angle_deg: float = 85.0
    mode: ExperimentMode = ExperimentMode.PHASE_TRACKED_SINE_AUTO_RESONANCE

    def validate(self) -> None:
        if not math.isfinite(self.target_angle_deg) or not 0.0 < self.target_angle_deg <= 85.0:
            raise ValueError("target angle must be greater than 0 and at most 85 degrees")
        if self.mode != ExperimentMode.PHASE_TRACKED_SINE_AUTO_RESONANCE:
            raise ValueError("PhaseTrackedSineDefinition requires Phase-Tracked Sinusoidal Auto-Resonance mode")
        for name, value in (
            ("pivot amplitude", self.amplitude_mm),
            ("phase", self.phase_deg),
            ("duration", self.duration_s),
        ):
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
        if self.amplitude_mm <= 0.0:
            raise ValueError("pivot amplitude must be greater than zero")
        if self.duration_s <= 0.0:
            raise ValueError("duration must be greater than zero")

    def partial_name(self) -> str:
        return self._label_prefix() + (
            f"PTAR_A{self._number(self.amplitude_mm)}mm_"
            f"Phi{self._phase_token()}deg_T{self._number(self.duration_s)}s"
        )

    def final_name(self, measured_initial_angle_deg: float) -> str:
        if not math.isfinite(measured_initial_angle_deg):
            raise ValueError("measured initial angle must be finite")
        if measured_initial_angle_deg < 0.0:
            angle_token = f"m{self._number(abs(measured_initial_angle_deg))}"
        elif measured_initial_angle_deg > 0.0:
            angle_token = f"p{self._number(measured_initial_angle_deg)}"
        else:
            angle_token = "0"
        return self._label_prefix() + (
            f"PTAR_A{self._number(self.amplitude_mm)}mm_"
            f"Phi{self._phase_token()}deg_"
            f"Theta{angle_token}deg_T{self._number(self.duration_s)}s"
        )

    def manifest_values(
        self,
        *,
        measured_initial_angle_deg: float | None,
        actual_elapsed_duration_s: float | None = None,
        completion_reason: str | None = None,
    ) -> dict[str, object]:
        return {
            "experiment_mode": self.mode.name,
            "commanded_amplitude_mm": self.amplitude_mm,
            "commanded_phase_deg": self.phase_deg,
            "requested_duration_s": self.duration_s,
            "target_angle_deg": self.target_angle_deg,
            "actual_elapsed_duration_s": actual_elapsed_duration_s,
            "measured_initial_angle_deg": measured_initial_angle_deg,
            "optional_label": self.label.strip(),
            "notes": self.notes.strip(),
            "generated_run_name": (
                self.partial_name()
                if measured_initial_angle_deg is None
                else self.final_name(measured_initial_angle_deg)
            ),
            "completion_reason": completion_reason,
        }

    def _label_prefix(self) -> str:
        label = re.sub(r"[^A-Za-z0-9._-]+", "_", self.label.strip()).strip("._-")
        return f"{label}_" if label else ""

    def _phase_token(self) -> str:
        if self.phase_deg < 0.0:
            return f"m{self._number(abs(self.phase_deg))}"
        if self.phase_deg > 0.0:
            return f"p{self._number(self.phase_deg)}"
        return "0"

    @staticmethod
    def _number(value: float) -> str:
        text = f"{value:.6f}".rstrip("0").rstrip(".")
        return text if text not in ("", "-0") else "0"


ExperimentDefinition = VerticalSineDefinition | AutoResonanceDefinition | PhaseTrackedSineDefinition
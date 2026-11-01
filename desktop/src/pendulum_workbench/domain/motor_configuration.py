"""Motor trajectory configuration snapshot retrieved from the STM32.

The firmware is the single source of truth for the runtime trajectory
configuration. Values are captured once per experiment through the correlated
maintenance ``config`` query and persisted in the experiment manifest.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


CONFIGURATION_SOURCE = "stm32_firmware"
STATUS_VERIFIED = "verified"
STATUS_UNAVAILABLE = "unavailable"

_RESPONSE_FIELDS = (
    "trajectory_max_speed_steps_s",
    "trajectory_max_speed_mm_s",
    "trajectory_acceleration_steps_s2",
    "trajectory_acceleration_mm_s2",
    "trajectory_start_speed_steps_s",
    "trajectory_start_speed_mm_s",
    "steps_per_mm",
    "ar_trace_rate_hz",
)


@dataclass(frozen=True, slots=True)
class MotorConfiguration:
    max_speed_steps_s: float | None
    max_speed_mm_s: float | None
    acceleration_steps_s2: float | None
    acceleration_mm_s2: float | None
    start_speed_steps_s: float | None
    start_speed_mm_s: float | None
    steps_per_mm: float | None
    ar_trace_rate_hz: int | None
    source: str
    status: str
    captured_at_utc: str | None
    error: str | None

    def to_manifest(self) -> dict[str, Any]:
        return {
            "max_speed_steps_s": self.max_speed_steps_s,
            "max_speed_mm_s": self.max_speed_mm_s,
            "acceleration_steps_s2": self.acceleration_steps_s2,
            "acceleration_mm_s2": self.acceleration_mm_s2,
            "start_speed_steps_s": self.start_speed_steps_s,
            "start_speed_mm_s": self.start_speed_mm_s,
            "steps_per_mm": self.steps_per_mm,
            "ar_trace_rate_hz": self.ar_trace_rate_hz,
            "source": self.source,
            "status": self.status,
            "captured_at_utc": self.captured_at_utc,
            "error": self.error,
        }

    @classmethod
    def from_response_fields(cls, fields: dict[str, str]) -> "MotorConfiguration":
        captured_at = datetime.now(timezone.utc).isoformat()
        try:
            values = {name: float(fields[name]) for name in _RESPONSE_FIELDS}
        except (KeyError, ValueError):
            return cls.unavailable(
                "unsupported_firmware",
                captured_at_utc=captured_at,
            )
        return cls(
            max_speed_steps_s=values["trajectory_max_speed_steps_s"],
            max_speed_mm_s=values["trajectory_max_speed_mm_s"],
            acceleration_steps_s2=values["trajectory_acceleration_steps_s2"],
            acceleration_mm_s2=values["trajectory_acceleration_mm_s2"],
            start_speed_steps_s=values["trajectory_start_speed_steps_s"],
            start_speed_mm_s=values["trajectory_start_speed_mm_s"],
            steps_per_mm=values["steps_per_mm"],
            ar_trace_rate_hz=int(values["ar_trace_rate_hz"]),
            source=CONFIGURATION_SOURCE,
            status=STATUS_VERIFIED,
            captured_at_utc=captured_at,
            error=None,
        )

    @classmethod
    def unavailable(
        cls,
        error: str,
        *,
        captured_at_utc: str | None = None,
    ) -> "MotorConfiguration":
        return cls(
            max_speed_steps_s=None,
            max_speed_mm_s=None,
            acceleration_steps_s2=None,
            acceleration_mm_s2=None,
            start_speed_steps_s=None,
            start_speed_mm_s=None,
            steps_per_mm=None,
            ar_trace_rate_hz=None,
            source=CONFIGURATION_SOURCE,
            status=STATUS_UNAVAILABLE,
            captured_at_utc=captured_at_utc,
            error=error,
        )

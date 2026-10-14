import math
import re
import csv
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pendulum_workbench.domain.models import HardwareStatus, TelemetrySample


class ParsedLineKind(str, Enum):
    TELEMETRY = "telemetry"
    HARDWARE_STATUS = "hardware_status"
    CONTROLLER_DIAGNOSTIC = "controller_diagnostic"
    DEVICE_MESSAGE = "device_message"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class ParsedLine:
    kind: ParsedLineKind
    telemetry: TelemetrySample | None = None
    hardware_status: HardwareStatus | None = None
    message: str = ""
    device_sample_index: int | None = None
    diagnostic_fields: dict[str, str] | None = None
    diagnostic_schema: tuple[str, ...] | None = None


class TelemetryParser:
    _field_pattern = re.compile(r"([A-Za-z_]+)=([^\s]+)")
    _recording_header = "time_sec,position_mm,angle_deg"
    _device_recording_header = "sample_index,time_s,angle_deg,position_mm"
    _autores_diagnostic_header = (
        "time_s", "theta_deg", "controller_mm", "motor_target_mm", "actual_mm",
        "target_steps", "actual_steps", "step_error",
    )
    _phase_sine_diagnostic_header = (
        "phase_sine_time_s", "angle_measured_deg", "angle_source", "phase2_raw_rad",
        "phase2_tracked_rad", "omega_2x_rad_s", "phase_magnitude", "phase_error_rad",
        "normalized_sine", "target_mm", "motor_target_mm", "actual_mm", "target_steps",
        "actual_steps", "step_error",
    )

    def __init__(self) -> None:
        self._controller_diagnostic_schema: tuple[str, ...] | None = None

    def parse_line(
        self,
        line: str,
        *,
        sample_index: int,
        elapsed_s: float,
        received_at: datetime,
        rx_line_index: int,
    ) -> ParsedLine:
        text = line.strip()
        if not text:
            return ParsedLine(ParsedLineKind.DEVICE_MESSAGE, message="Empty serial line")

        if text.startswith("> "):
            text = text[2:].lstrip()

        if text in ("live telemetry enabled", "live telemetry disabled"):
            return ParsedLine(ParsedLineKind.DEVICE_MESSAGE, message=text)

        if self._recording_header in text or text.startswith(self._device_recording_header):
            self._controller_diagnostic_schema = None
            return ParsedLine(ParsedLineKind.DEVICE_MESSAGE, message="Telemetry CSV header")

        fields = next(csv.reader([text]), [])
        normalized_header = tuple(field.strip() for field in fields)
        if normalized_header in (
            self._autores_diagnostic_header,
            self._phase_sine_diagnostic_header,
        ):
            self._controller_diagnostic_schema = normalized_header
            return ParsedLine(
                ParsedLineKind.DEVICE_MESSAGE,
                message="Controller diagnostic CSV header",
                diagnostic_schema=normalized_header,
            )

        status_offset = text.find("status:")
        if status_offset >= 0:
            return self._parse_status(text[status_offset + len("status:"):], received_at)

        if text.startswith("switches:"):
            return self._parse_switches(text[len("switches:"):], received_at)

        if text.startswith("live "):
            self._controller_diagnostic_schema = None
            return self._parse_live(
                text,
                sample_index=sample_index,
                elapsed_s=elapsed_s,
                received_at=received_at,
                rx_line_index=rx_line_index,
            )

        if text.startswith("telemetry "):
            self._controller_diagnostic_schema = None
            return self._parse_device_telemetry(
                text,
                elapsed_s=elapsed_s,
                received_at=received_at,
                rx_line_index=rx_line_index,
            )

        if "," in text:
            if self._controller_diagnostic_schema is not None:
                return self._parse_controller_diagnostic(text)
            return self._parse_csv_sample(
                text,
                sample_index=sample_index,
                elapsed_s=elapsed_s,
                received_at=received_at,
                rx_line_index=rx_line_index,
            )

        self._controller_diagnostic_schema = None
        return ParsedLine(ParsedLineKind.DEVICE_MESSAGE, message=text)

    def _parse_controller_diagnostic(self, text: str) -> ParsedLine:
        schema = self._controller_diagnostic_schema
        fields = next(csv.reader([text]), [])
        if schema is None or len(fields) != len(schema):
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Invalid controller diagnostic field count ({len(fields)}): {text}",
            )
        values = dict(zip(schema, (field.strip() for field in fields)))
        numeric_fields = (
            self._phase_sine_diagnostic_header
            if schema == self._phase_sine_diagnostic_header
            else self._autores_diagnostic_header
        )
        for name in numeric_fields:
            if name == "angle_source":
                continue
            value = values[name]
            if name in ("angle_measured_deg", "phase2_raw_rad") and value.lower() in (
                "nan", "na", "unavailable"
            ):
                continue
            try:
                number = float(value)
            except ValueError:
                return ParsedLine(
                    ParsedLineKind.MALFORMED,
                    message=f"Invalid controller diagnostic value for {name}: {text}",
                )
            if not math.isfinite(number):
                if name in ("angle_measured_deg", "phase2_raw_rad") and math.isnan(number):
                    continue
                return ParsedLine(
                    ParsedLineKind.MALFORMED,
                    message=f"Non-finite controller diagnostic value for {name}: {text}",
                )
        return ParsedLine(
            ParsedLineKind.CONTROLLER_DIAGNOSTIC,
            diagnostic_fields=values,
            diagnostic_schema=schema,
        )

    def _parse_device_telemetry(
        self,
        text: str,
        *,
        elapsed_s: float,
        received_at: datetime,
        rx_line_index: int,
    ) -> ParsedLine:
        fields = dict(self._field_pattern.findall(text))
        required = ("sample_index", "time_s", "angle_deg", "position_mm")
        missing = [field for field in required if field not in fields]
        if missing:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Incomplete device telemetry; missing {', '.join(missing)}: {text}",
            )
        try:
            device_sample_index = int(fields["sample_index"])
            device_time_s = self._finite_float(fields["time_s"])
            angle_deg = self._optional_measurement(fields["angle_deg"])
            position_mm = self._finite_float(fields["position_mm"])
            if device_sample_index <= 0:
                raise ValueError("sample_index must be positive")
        except ValueError as error:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Invalid device telemetry ({error}): {text}",
            )

        sample = TelemetrySample(
            sample_index=device_sample_index,
            elapsed_s=device_time_s,
            angle_deg=angle_deg,
            position_mm=position_mm,
            source="real",
            device_time_s=device_time_s,
            received_at=received_at,
            rx_line_index=rx_line_index,
            device_sample_index=device_sample_index,
            received_elapsed_s=elapsed_s,
        )
        return ParsedLine(
            ParsedLineKind.TELEMETRY,
            telemetry=sample,
            device_sample_index=device_sample_index,
        )

    def _parse_live(
        self,
        text: str,
        *,
        sample_index: int,
        elapsed_s: float,
        received_at: datetime,
        rx_line_index: int,
    ) -> ParsedLine:
        fields = dict(self._field_pattern.findall(text))
        angle_field = fields.get("angle_deg", fields.get("angle"))
        required = ("time", "position_mm")
        if any(field not in fields for field in required):
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Incomplete live telemetry: {text}",
            )
        if angle_field is None:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Incomplete live telemetry: {text}",
            )

        try:
            device_time = self._finite_float(fields["time"])
            angle = self._optional_measurement(angle_field)
            position = self._finite_float(fields["position_mm"])
        except ValueError as error:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Invalid live telemetry ({error}): {text}",
            )

        sample = TelemetrySample(
            sample_index=sample_index,
            elapsed_s=device_time,
            angle_deg=angle,
            position_mm=position,
            source="real",
            device_time_s=device_time,
            received_at=received_at,
            rx_line_index=rx_line_index,
            received_elapsed_s=elapsed_s,
        )
        message = "" if angle is not None else "angle sensor unavailable"
        return ParsedLine(ParsedLineKind.TELEMETRY, telemetry=sample, message=message)

    def _parse_csv_sample(
        self,
        text: str,
        *,
        sample_index: int,
        elapsed_s: float,
        received_at: datetime,
        rx_line_index: int,
    ) -> ParsedLine:
        fields = [field.strip() for field in text.split(",")]
        if len(fields) == 4:
            try:
                device_sample_index = int(fields[0])
                device_time = self._finite_float(fields[1])
                angle = self._optional_measurement(fields[2])
                position = self._finite_float(fields[3])
                if device_sample_index <= 0:
                    raise ValueError("sample_index must be positive")
            except ValueError as error:
                return ParsedLine(
                    ParsedLineKind.MALFORMED,
                    message=f"Invalid indexed CSV telemetry ({error}): {text}",
                )
            sample = TelemetrySample(
                sample_index=device_sample_index,
                elapsed_s=device_time,
                angle_deg=angle,
                position_mm=position,
                source="real",
                device_time_s=device_time,
                received_at=received_at,
                rx_line_index=rx_line_index,
                device_sample_index=device_sample_index,
                received_elapsed_s=elapsed_s,
            )
            return ParsedLine(
                ParsedLineKind.TELEMETRY,
                telemetry=sample,
                device_sample_index=device_sample_index,
            )
        if len(fields) != 3:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Unexpected telemetry field count ({len(fields)}): {text}",
            )

        try:
            device_time, position, angle = (self._finite_float(field) for field in fields)
        except ValueError as error:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Invalid CSV telemetry ({error}): {text}",
            )

        sample = TelemetrySample(
            sample_index=sample_index,
            elapsed_s=device_time,
            angle_deg=angle,
            position_mm=position,
            source="real",
            device_time_s=device_time,
            received_at=received_at,
            rx_line_index=rx_line_index,
            received_elapsed_s=elapsed_s,
        )
        return ParsedLine(ParsedLineKind.TELEMETRY, telemetry=sample)

    def _parse_status(self, text: str, received_at: datetime) -> ParsedLine:
        fields = dict(self._field_pattern.findall(text))
        if "active_mode" in fields:
            required = (
                "homed",
                "upper_limit",
                "lower_limit",
                "fault",
                "travel_span_steps",
                "position_steps",
                "steps_per_mm",
            )
            if any(field not in fields for field in required):
                return ParsedLine(
                    ParsedLineKind.MALFORMED,
                    message=f"Incomplete operational status: status:{text.strip()}",
                )
            try:
                homed = self._bit(fields["homed"])
                upper = self._bit(fields["upper_limit"])
                lower = self._bit(fields["lower_limit"])
                fault_value = int(fields["fault"])
                span = int(fields["travel_span_steps"])
                position_steps = int(fields["position_steps"])
                steps_per_mm = self._finite_float(fields["steps_per_mm"])
                position_mm = position_steps / steps_per_mm if steps_per_mm > 0 else None
            except ValueError as error:
                return ParsedLine(
                    ParsedLineKind.MALFORMED,
                    message=f"Invalid operational status ({error}): status:{text.strip()}",
                )
            mode_names = {
                "idle": "Idle",
                "manual_up": "Manual motion up",
                "manual_down": "Manual motion down",
                "manual_stepping": "Manual stepping",
                "parametric_closed_loop": "Parametric closed loop",
                "parametric_test": "Parametric test (no motion)",
            }
            status = HardwareStatus(
                homed=homed,
                upper_limit=upper,
                lower_limit=lower,
                active_motion_mode=mode_names.get(fields["active_mode"], fields["active_mode"]),
                fault=f"Fault {fault_value}" if fault_value else None,
                fault_known=True,
                updated_at=received_at,
                travel_span_steps=span,
                position_steps=position_steps,
                steps_per_mm=steps_per_mm,
                position_mm=position_mm,
            )
            return ParsedLine(
                ParsedLineKind.HARDWARE_STATUS,
                hardware_status=status,
                message="Operational status updated",
            )

        required = (
            "run", "homed", "sine", "parametric", "parametric_test", "closed_loop", "fault"
        )
        if any(field not in fields for field in required):
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Incomplete hardware status: status:{text.strip()}",
            )
        try:
            run = self._bit(fields["run"])
            homed = self._bit(fields["homed"])
            sine = self._bit(fields["sine"])
            parametric = self._bit(fields["parametric"])
            parametric_test = self._bit(fields["parametric_test"])
            closed_loop = self._bit(fields["closed_loop"])
            fault_value = int(fields["fault"])
            upper = self._optional_bit(fields.get("upper"))
            lower = self._optional_bit(fields.get("lower"))
        except ValueError as error:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Invalid hardware status ({error}): status:{text.strip()}",
            )

        mode = (
            "Parametric closed loop" if closed_loop else
            "Sine" if sine else
            "Parametric" if parametric else
            "Parametric test (no motion)" if parametric_test else
            "Manual stepping" if run else
            "Idle"
        )
        hardware_status = HardwareStatus(
            homed=homed,
            upper_limit=upper,
            lower_limit=lower,
            active_motion_mode=mode,
            fault=f"Fault {fault_value}" if fault_value else None,
            fault_known=True,
            updated_at=received_at,
        )
        return ParsedLine(
            ParsedLineKind.HARDWARE_STATUS,
            hardware_status=hardware_status,
            message="Hardware status updated",
        )

    def _parse_switches(self, text: str, received_at: datetime) -> ParsedLine:
        fields = dict(self._field_pattern.findall(text))
        try:
            upper = self._optional_bit(fields.get("upper"))
            lower = self._optional_bit(fields.get("lower"))
        except ValueError as error:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Invalid switch status ({error}): switches:{text.strip()}",
            )
        if upper is None or lower is None:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Incomplete switch status: switches:{text.strip()}",
            )
        hardware_status = HardwareStatus(
            upper_limit=upper,
            lower_limit=lower,
            updated_at=received_at,
        )
        return ParsedLine(
            ParsedLineKind.HARDWARE_STATUS,
            hardware_status=hardware_status,
            message="Limit-switch status updated",
        )

    @staticmethod
    def _finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("value is not finite")
        return number

    @staticmethod
    def _optional_measurement(value: str) -> float | None:
        if value.lower() in ("nan", "na", "unavailable"):
            return None
        return TelemetryParser._finite_float(value)

    @staticmethod
    def _bit(value: str) -> bool:
        if value not in ("0", "1"):
            raise ValueError(f"expected 0 or 1, got {value!r}")
        return value == "1"

    @classmethod
    def _optional_bit(cls, value: str | None) -> bool | None:
        return None if value is None else cls._bit(value)
import math
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from pendulum_workbench.domain.models import HardwareStatus, TelemetrySample


class ParsedLineKind(str, Enum):
    TELEMETRY = "telemetry"
    HARDWARE_STATUS = "hardware_status"
    DEVICE_MESSAGE = "device_message"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class ParsedLine:
    kind: ParsedLineKind
    telemetry: TelemetrySample | None = None
    hardware_status: HardwareStatus | None = None
    message: str = ""


class TelemetryParser:
    _field_pattern = re.compile(r"([A-Za-z_]+)=([^\s]+)")
    _recording_header = "time_sec,position_mm,angle_deg"

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

        if self._recording_header in text:
            return ParsedLine(ParsedLineKind.DEVICE_MESSAGE, message="Telemetry CSV header")

        status_offset = text.find("status:")
        if status_offset >= 0:
            return self._parse_status(text[status_offset + len("status:"):], received_at)

        if text.startswith("switches:"):
            return self._parse_switches(text[len("switches:"):], received_at)

        if text.startswith("live "):
            return self._parse_live(
                text,
                sample_index=sample_index,
                elapsed_s=elapsed_s,
                received_at=received_at,
                rx_line_index=rx_line_index,
            )

        if "," in text:
            return self._parse_csv_sample(
                text,
                sample_index=sample_index,
                elapsed_s=elapsed_s,
                received_at=received_at,
                rx_line_index=rx_line_index,
            )

        return ParsedLine(ParsedLineKind.DEVICE_MESSAGE, message=text)

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
        required = ("time", "angle_deg", "position_mm")
        if any(field not in fields for field in required):
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Incomplete live telemetry: {text}",
            )

        try:
            device_time = self._finite_float(fields["time"])
            angle = self._finite_float(fields["angle_deg"])
            position = self._finite_float(fields["position_mm"])
        except ValueError as error:
            return ParsedLine(
                ParsedLineKind.MALFORMED,
                message=f"Invalid live telemetry ({error}): {text}",
            )

        sample = TelemetrySample(
            sample_index=sample_index,
            elapsed_s=elapsed_s,
            angle_deg=angle,
            position_mm=position,
            source="real",
            device_time_s=device_time,
            received_at=received_at,
            rx_line_index=rx_line_index,
        )
        return ParsedLine(ParsedLineKind.TELEMETRY, telemetry=sample)

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
            elapsed_s=elapsed_s,
            angle_deg=angle,
            position_mm=position,
            source="real",
            device_time_s=device_time,
            received_at=received_at,
            rx_line_index=rx_line_index,
        )
        return ParsedLine(ParsedLineKind.TELEMETRY, telemetry=sample)

    def _parse_status(self, text: str, received_at: datetime) -> ParsedLine:
        fields = dict(self._field_pattern.findall(text))
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
    def _bit(value: str) -> bool:
        if value not in ("0", "1"):
            raise ValueError(f"expected 0 or 1, got {value!r}")
        return value == "1"

    @classmethod
    def _optional_bit(cls, value: str | None) -> bool | None:
        return None if value is None else cls._bit(value)
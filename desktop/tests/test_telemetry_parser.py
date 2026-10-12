import unittest
from datetime import datetime, timezone

from pendulum_workbench.domain.models import HardwareStatus
from pendulum_workbench.infrastructure.telemetry_parser import (
    ParsedLineKind,
    TelemetryParser,
)


class TelemetryParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = TelemetryParser()
        self.received_at = datetime(2026, 10, 9, tzinfo=timezone.utc)

    def parse(self, line: str):
        return self.parser.parse_line(
            line,
            sample_index=4,
            elapsed_s=1.25,
            received_at=self.received_at,
            rx_line_index=12,
        )

    def test_parses_live_telemetry_using_device_time_as_primary_time(self) -> None:
        result = self.parse(
            "live time=8.320 angle_deg=-2.50 position_mm=1.250 position_steps=50 homed=1"
        )

        self.assertEqual(result.kind, ParsedLineKind.TELEMETRY)
        self.assertEqual(result.telemetry.elapsed_s, 8.32)
        self.assertEqual(result.telemetry.device_time_s, 8.32)
        self.assertEqual(result.telemetry.received_elapsed_s, 1.25)
        self.assertEqual(result.telemetry.angle_deg, -2.5)
        self.assertEqual(result.telemetry.position_mm, 1.25)
        self.assertEqual(result.telemetry.rx_line_index, 12)

    def test_parses_indexed_device_telemetry_and_preserves_receive_provenance(self) -> None:
        result = self.parse(
            "telemetry sample_index=108 time_s=12.500 angle_deg=-2.50 position_mm=1.250"
        )

        self.assertEqual(result.kind, ParsedLineKind.TELEMETRY)
        self.assertEqual(result.device_sample_index, 108)
        self.assertEqual(result.telemetry.sample_index, 108)
        self.assertEqual(result.telemetry.device_sample_index, 108)
        self.assertEqual(result.telemetry.time_s, 12.5)
        self.assertEqual(result.telemetry.received_elapsed_s, 1.25)
        self.assertEqual(result.telemetry.rx_line_index, 12)

    def test_parses_indexed_telemetry_prefixed_by_firmware_console_prompt(self) -> None:
        result = self.parse(
            "> telemetry sample_index=109 time_s=12.520 angle_deg=2.5 position_mm=1.260"
        )

        self.assertEqual(result.kind, ParsedLineKind.TELEMETRY)
        self.assertEqual(result.device_sample_index, 109)
        self.assertEqual(result.telemetry.position_mm, 1.26)

    def test_live_enable_acknowledgement_is_a_device_message_not_telemetry(self) -> None:
        result = self.parse("live telemetry enabled")

        self.assertEqual(result.kind, ParsedLineKind.DEVICE_MESSAGE)
        self.assertEqual(result.message, "live telemetry enabled")

    def test_missing_device_sample_index_is_malformed(self) -> None:
        result = self.parse("telemetry time_s=12.500 angle_deg=2.5 position_mm=1.25")

        self.assertEqual(result.kind, ParsedLineKind.MALFORMED)
        self.assertIn("sample_index", result.message)

    def test_as5600_nan_is_a_missing_measurement_not_a_dropped_sample(self) -> None:
        result = self.parse(
            "telemetry sample_index=109 time_s=12.520 angle_deg=NaN position_mm=1.250"
        )

        self.assertEqual(result.kind, ParsedLineKind.TELEMETRY)
        self.assertIsNone(result.telemetry.angle_deg)
        self.assertEqual(result.telemetry.position_mm, 1.25)

    def test_parses_existing_three_column_recording_rows(self) -> None:
        result = self.parse("0.040,1.500,-3.250")

        self.assertEqual(result.kind, ParsedLineKind.TELEMETRY)
        self.assertEqual(result.telemetry.device_time_s, 0.04)
        self.assertEqual(result.telemetry.position_mm, 1.5)
        self.assertEqual(result.telemetry.angle_deg, -3.25)

    def test_parses_status_line_embedded_after_sensor_status(self) -> None:
        result = self.parse(
            "angle=201/4095 status: run=0 homed=1 sine=0 parametric=1 "
            "parametric_test=0 closed_loop=0 fault=0 upper=0 lower=1 span=5216 "
            "position=0 target=0 scale=40.000"
        )

        self.assertEqual(result.kind, ParsedLineKind.HARDWARE_STATUS)
        self.assertEqual(
            result.hardware_status,
            HardwareStatus(
                homed=True,
                upper_limit=False,
                lower_limit=True,
                active_motion_mode="Parametric",
                fault=None,
                fault_known=True,
                updated_at=self.received_at,
            ),
        )

    def test_parses_compact_operational_status_without_measurement_fields(self) -> None:
        result = self.parse(
            "status: homed=1 upper_limit=0 lower_limit=1 fault=0 active_mode=manual_up "
            "travel_span_steps=1200 position_steps=60 steps_per_mm=40.000"
        )

        self.assertEqual(result.kind, ParsedLineKind.HARDWARE_STATUS)
        self.assertEqual(result.hardware_status.active_motion_mode, "Manual motion up")
        self.assertEqual(result.hardware_status.position_steps, 60)
        self.assertEqual(result.hardware_status.travel_span_steps, 1200)
        self.assertEqual(result.hardware_status.position_mm, 1.5)

    def test_classifies_live_telemetry_missing_required_position_as_malformed(self) -> None:
        result = self.parse("live time=1.200 angle_deg=2.0")

        self.assertEqual(result.kind, ParsedLineKind.MALFORMED)
        self.assertIn("Incomplete live telemetry", result.message)

    def test_legacy_unavailable_angle_remains_telemetry_with_missing_measurement(self) -> None:
        result = self.parse("live time=1.200 angle=unavailable position_mm=0.000")

        self.assertEqual(result.kind, ParsedLineKind.TELEMETRY)
        self.assertIsNone(result.telemetry.angle_deg)
        self.assertEqual(result.telemetry.device_time_s, 1.2)
        self.assertEqual(result.message, "angle sensor unavailable")

    def test_classifies_console_and_debug_text_as_device_messages(self) -> None:
        result = self.parse("RX DEBUG len=4 bytes=68 65 6C 70")

        self.assertEqual(result.kind, ParsedLineKind.DEVICE_MESSAGE)

    def test_rejects_nonfinite_csv_telemetry(self) -> None:
        result = self.parse("0.040,NaN,1.000")

        self.assertEqual(result.kind, ParsedLineKind.MALFORMED)
        self.assertIn("not finite", result.message)


if __name__ == "__main__":
    unittest.main()
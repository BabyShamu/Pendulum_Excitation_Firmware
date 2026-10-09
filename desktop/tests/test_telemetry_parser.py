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

    def test_parses_live_telemetry_with_receive_elapsed_time(self) -> None:
        result = self.parse(
            "live time=8.320 angle_deg=-2.50 position_mm=1.250 position_steps=50 homed=1"
        )

        self.assertEqual(result.kind, ParsedLineKind.TELEMETRY)
        self.assertEqual(result.telemetry.elapsed_s, 1.25)
        self.assertEqual(result.telemetry.device_time_s, 8.32)
        self.assertEqual(result.telemetry.angle_deg, -2.5)
        self.assertEqual(result.telemetry.position_mm, 1.25)
        self.assertEqual(result.telemetry.rx_line_index, 12)

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

    def test_classifies_incomplete_live_telemetry_as_malformed(self) -> None:
        result = self.parse("live time=1.200 angle=unavailable position_mm=0.000")

        self.assertEqual(result.kind, ParsedLineKind.MALFORMED)
        self.assertIn("Incomplete live telemetry", result.message)

    def test_classifies_console_and_debug_text_as_device_messages(self) -> None:
        result = self.parse("RX DEBUG len=4 bytes=68 65 6C 70")

        self.assertEqual(result.kind, ParsedLineKind.DEVICE_MESSAGE)

    def test_rejects_nonfinite_csv_telemetry(self) -> None:
        result = self.parse("0.040,NaN,1.000")

        self.assertEqual(result.kind, ParsedLineKind.MALFORMED)
        self.assertIn("not finite", result.message)


if __name__ == "__main__":
    unittest.main()
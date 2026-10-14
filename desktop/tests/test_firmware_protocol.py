import unittest

from pendulum_workbench.infrastructure.firmware_protocol import FirmwareProtocolParser


class FirmwareProtocolParserTests(unittest.TestCase):
    def test_parses_correlated_maintenance_response(self) -> None:
        response = FirmwareProtocolParser.parse_maintenance_response(
            "maintenance: id=42 type=switches ok=1 upper=0 lower=1\r\n"
        )

        self.assertIsNotNone(response)
        self.assertEqual(response.request_id, 42)
        self.assertEqual(response.response_type, "switches")
        self.assertTrue(response.ok)
        self.assertEqual(response.fields["upper"], "0")
        self.assertEqual(response.fields["lower"], "1")

    def test_rejects_unidentified_or_malformed_maintenance_line(self) -> None:
        self.assertIsNone(
            FirmwareProtocolParser.parse_maintenance_response(
                "maintenance: type=status ok=1"
            )
        )
        self.assertIsNone(
            FirmwareProtocolParser.parse_maintenance_response(
                "maintenance: id=0 type=status ok=1"
            )
        )
        self.assertIsNone(FirmwareProtocolParser.parse_maintenance_response("live time=1"))

    def test_reports_firmware_rejection_without_treating_it_as_success(self) -> None:
        response = FirmwareProtocolParser.parse_maintenance_response(
            "maintenance: id=7 type=home ok=0 homed=0 error=blocking_fault"
        )

        self.assertIsNotNone(response)
        self.assertFalse(response.ok)
        self.assertEqual(response.fields["error"], "blocking_fault")

    def test_operational_event_and_diagnostic_are_separate(self) -> None:
        event = FirmwareProtocolParser.parse_device_event(
            "event: type=limit_state_changed upper=1 lower=0"
        )
        diagnostic = FirmwareProtocolParser.parse_diagnostic(
            "diag: phase=idle irq=12"
        )

        self.assertEqual(event.name, "limit_state_changed")
        self.assertEqual(event.fields["upper"], "1")
        self.assertEqual(diagnostic.text, "phase=idle irq=12")
        self.assertIsNone(FirmwareProtocolParser.parse_device_event("diag: irq=12"))
        self.assertIsNone(FirmwareProtocolParser.parse_diagnostic("event: type=fault_cleared"))

    def test_events_and_diagnostics_have_separate_parsers(self) -> None:
        event = FirmwareProtocolParser.parse_device_event(
            "event: type=manual_motion_stopped direction=up reason=upper_limit"
        )
        diagnostic = FirmwareProtocolParser.parse_diagnostic(
            "diag: tim6_irq=18 phase=idle"
        )

        self.assertEqual(event.name, "manual_motion_stopped")
        self.assertEqual(event.fields["reason"], "upper_limit")
        self.assertEqual(diagnostic.text, "tim6_irq=18 phase=idle")
        self.assertIsNone(FirmwareProtocolParser.parse_device_event("diag: tim6_irq=18"))
        self.assertIsNone(FirmwareProtocolParser.parse_diagnostic("event: type=fault_cleared"))

    def test_as5600_diagnostic_event_fields_remain_parseable(self) -> None:
        unavailable = FirmwareProtocolParser.parse_device_event(
            "event: type=angle_sensor_unavailable failures=3 hal_status=1 "
            "hal_error=0x00000004 hal_error_name=AF_NACK recovery_attempted=1 "
            "sda=1 scl=0 sda_pin=PB9 scl_pin=PB8 uptime_ms=183421"
        )
        recovery = FirmwareProtocolParser.parse_device_event(
            "event: type=angle_sensor_recovery attempt=4 result=failed "
            "hal_error=0x00000020 hal_error_name=TIMEOUT sda_before=1 "
            "sda_after_pulses=1 sda_after_reinit=0 uptime_ms=183612"
        )
        restored = FirmwareProtocolParser.parse_device_event(
            "event: type=angle_sensor_restored outage_ms=191 recovery_count=4 "
            "hal_error=0x00000000 hal_error_name=NONE uptime_ms=183803"
        )

        self.assertEqual(unavailable.name, "angle_sensor_unavailable")
        self.assertEqual(unavailable.fields["hal_error"], "0x00000004")
        self.assertEqual(unavailable.fields["hal_error_name"], "AF_NACK")
        self.assertEqual(unavailable.fields["sda_pin"], "PB9")
        self.assertEqual(unavailable.fields["scl_pin"], "PB8")
        self.assertEqual(recovery.name, "angle_sensor_recovery")
        self.assertEqual(recovery.fields["result"], "failed")
        self.assertEqual(recovery.fields["sda_after_reinit"], "0")
        self.assertEqual(restored.name, "angle_sensor_restored")
        self.assertEqual(restored.fields["outage_ms"], "191")

    def test_fault_cleared_event_preserves_previous_fault_code(self) -> None:
        event = FirmwareProtocolParser.parse_device_event(
            "event: type=fault_cleared previous_fault=3"
        )

        self.assertEqual(event.name, "fault_cleared")
        self.assertEqual(event.fields["previous_fault"], "3")
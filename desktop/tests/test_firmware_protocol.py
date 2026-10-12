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
import base64
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from pendulum_workbench.infrastructure.raw_capture import RawSerialCapture


class RawSerialCaptureTests(unittest.TestCase):
    def test_capture_preserves_payload_terminator_index_and_receive_times(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            capture = RawSerialCapture(Path(temporary_directory), "test-session")
            timestamp = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)

            first_index = capture.write_line(b"live time=1.0", b"\r\n", 0.1, timestamp)
            second_index = capture.write_line(b"RX DEBUG", b"\n", 0.2, timestamp)
            capture.close()

            records = [
                json.loads(line)
                for line in capture.path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual((first_index, second_index), (1, 2))
            self.assertEqual(records[0]["rx_line_index"], 1)
            self.assertEqual(records[0]["terminator"], "CRLF")
            self.assertEqual(
                base64.b64decode(records[0]["raw_bytes_base64"]), b"live time=1.0"
            )
            self.assertEqual(records[0]["received_elapsed_s"], 0.1)
            self.assertEqual(records[0]["received_at_utc"], timestamp.isoformat())
            self.assertEqual(records[1]["terminator"], "LF")
            self.assertFalse(records[1]["partial"])

    def test_partial_record_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            capture = RawSerialCapture(Path(temporary_directory), "partial-session")
            capture.write_line(b"incomplete", b"", 0.5, partial=True)
            capture.close()

            record = json.loads(capture.path.read_text(encoding="utf-8"))
            self.assertEqual(record["terminator"], "NONE")
            self.assertTrue(record["partial"])


if __name__ == "__main__":
    unittest.main()
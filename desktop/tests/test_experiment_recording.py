import base64
import csv
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pendulum_workbench.application.experiment_recording import ExperimentRecordingService
from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    RecordingState,
    TelemetrySample,
)
from pendulum_workbench.experiments.repository import ExperimentRepository
from pendulum_workbench.infrastructure.serial_client import (
    ReceivedSerialLine,
    SerialTelemetryClient,
)


class ExperimentRecordingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.qt_app.processEvents()
        return predicate()

    def test_mock_recording_writes_manifest_normalized_rows_and_reopens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state = ApplicationStateModel()
            state.set_mock_source_active(True)
            repository = ExperimentRepository(Path(temporary_directory))
            service = ExperimentRecordingService(state, SerialTelemetryClient(), repository)
            self.addCleanup(service.shutdown)

            experiment_id = service.start_recording("Free decay", "release from rest")
            self.assertEqual(state.snapshot.recording, RecordingState.STARTING)
            self.assertTrue(self.wait_for(lambda: state.snapshot.recording == RecordingState.RECORDING))
            session = service.active_session
            self.assertIsNotNone(session)
            self.assertEqual(session.experiment_id, experiment_id)
            state.publish_sample(TelemetrySample(1, 0.1, -1.5, 2.0, source="mock"))
            state.publish_sample(TelemetrySample(2, 0.2, -1.2, 2.2, source="mock"))
            service.stop_recording()
            self.assertEqual(state.snapshot.recording, RecordingState.STOPPING)
            self.assertTrue(self.wait_for(lambda: state.snapshot.recording == RecordingState.SAVED))

            self.assertEqual(state.snapshot.recording, RecordingState.SAVED)
            manifest = json.loads((session.directory / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["completion_status"], "COMPLETED")
            self.assertEqual(manifest["experiment_id"], session.experiment_id)
            self.assertEqual(manifest["name"], "Free decay")
            self.assertEqual(manifest["notes"], "release from rest")
            self.assertEqual(manifest["data_source"]["mode"], "Mock")
            self.assertIsNotNone(manifest["application_version"])
            self.assertIsNone(manifest["firmware_version"])
            self.assertEqual(
                manifest["configuration"]["physical_parameters"]["pendulum_length_m"]["status"],
                "TBD",
            )

            raw_path = session.directory / "serial_capture.log"
            self.assertTrue(raw_path.exists())
            self.assertEqual(raw_path.read_text(encoding="utf-8"), "")
            with (session.directory / "telemetry.csv").open(
                "r", encoding="utf-8", newline=""
            ) as telemetry_file:
                rows = list(csv.DictReader(telemetry_file))
            self.assertEqual(len(rows), 2)
            self.assertEqual(rows[0]["angle_deg"], "-1.5")
            self.assertEqual(rows[1]["position_mm"], "2.2")
            self.assertEqual(rows[0]["quality"], "valid")

            reopened = repository.load(session.directory)
            self.assertEqual(len(reopened.telemetry), 2)
            self.assertEqual(reopened.telemetry[0].angle_deg, -1.5)
            self.assertEqual(reopened.telemetry[1].position_mm, 2.2)

    def test_real_recording_mirrors_raw_serial_line_without_mutating_source_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state = ApplicationStateModel()
            state.set_data_source(DataSourceMode.REAL)
            state.set_connection(ConnectionState.CONNECTED, "COM6")
            serial_client = SerialTelemetryClient()
            repository = ExperimentRepository(Path(temporary_directory))
            service = ExperimentRecordingService(state, serial_client, repository)
            self.addCleanup(service.shutdown)
            timestamp = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)
            payload = b"live time=0.100 angle_deg=-1.500 position_mm=2.000"

            experiment_id = service.start_recording("Real run", "board reset before start")
            self.assertTrue(self.wait_for(lambda: state.snapshot.recording == RecordingState.RECORDING))
            session = service.active_session
            self.assertIsNotNone(session)
            self.assertEqual(session.experiment_id, experiment_id)
            serial_client.raw_line_received.emit(
                ReceivedSerialLine(
                    payload=payload,
                    received_at=timestamp,
                    received_elapsed_s=0.1,
                    rx_line_index=42,
                    terminator=b"\r\n",
                )
            )
            state.publish_sample(
                TelemetrySample(
                    1,
                    0.1,
                    -1.5,
                    2.0,
                    source="real",
                    device_time_s=0.1,
                    received_at=timestamp,
                    rx_line_index=42,
                )
            )
            service.stop_recording()
            self.assertTrue(self.wait_for(lambda: state.snapshot.recording == RecordingState.SAVED))

            record = json.loads(
                (session.directory / "serial_capture.log").read_text(encoding="utf-8")
            )
            self.assertEqual(base64.b64decode(record["raw_bytes_base64"]), payload)
            self.assertEqual(record["rx_line_index"], 42)
            self.assertEqual(record["terminator"], "CRLF")
            self.assertEqual(record["received_at_utc"], timestamp.isoformat())

    def test_connection_loss_marks_active_recording_interrupted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            state = ApplicationStateModel()
            state.set_data_source(DataSourceMode.REAL)
            state.set_connection(ConnectionState.CONNECTED, "COM6")
            serial_client = SerialTelemetryClient()
            service = ExperimentRecordingService(
                state,
                serial_client,
                ExperimentRepository(Path(temporary_directory)),
            )
            self.addCleanup(service.shutdown)

            experiment_id = service.start_recording("Interrupted run", "")
            self.assertTrue(self.wait_for(lambda: state.snapshot.recording == RecordingState.RECORDING))
            session = service.active_session
            self.assertIsNotNone(session)
            self.assertEqual(session.experiment_id, experiment_id)
            serial_client.connection_closed.emit("simulated cable removal")
            self.assertTrue(
                self.wait_for(lambda: state.snapshot.recording == RecordingState.INTERRUPTED)
            )

            manifest = json.loads((session.directory / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["completion_status"], "INTERRUPTED")
            self.assertIn("simulated cable removal", manifest["interruption_reason"])
            self.assertEqual(state.snapshot.recording, RecordingState.INTERRUPTED)


if __name__ == "__main__":
    unittest.main()
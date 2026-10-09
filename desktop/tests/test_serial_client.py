import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import serial
from PySide6.QtCore import QCoreApplication

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    MotionState,
)
from pendulum_workbench.infrastructure.serial_client import (
    SerialLineFramer,
    SerialTelemetryClient,
    SerialWorker,
)


class SerialLineFramerTests(unittest.TestCase):
    def test_frames_crlf_lf_cr_and_preserves_final_partial(self) -> None:
        framer = SerialLineFramer()

        first = framer.feed(b"one\r")
        second = framer.feed(b"\ntwo\nthree\rfour")
        final = framer.finish()

        self.assertEqual(first, [])
        self.assertEqual(
            [(line.payload, line.terminator) for line in second],
            [(b"one", b"\r\n"), (b"two", b"\n"), (b"three", b"\r")],
        )
        self.assertEqual(final, [type(final[0])(b"four", b"", partial=True)])


class FakeSerial:
    def __init__(self, port: str, baud_rate: int, **kwargs: object) -> None:
        del port, baud_rate, kwargs
        self._chunks = [
            b"live time=1.000 angle_deg=2.000 position_mm=3.000\r\n"
            b"status: run=0 homed=1 sine=0 parametric=0 parametric_test=0 "
            b"closed_loop=0 fault=0 upper=0 lower=1\r\n"
            b"live time=1.020 angle=unavailable position_mm=3.000\r\npartial",
        ]
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        return len(data)

    def read(self, size: int) -> bytes:
        del size
        if self._chunks:
            return self._chunks.pop(0)
        time.sleep(0.005)
        return b""

    def close(self) -> None:
        self.closed = True


class DroppedSerial(FakeSerial):
    def read(self, size: int) -> bytes:
        if self._chunks:
            return super().read(size)
        raise serial.SerialException("simulated cable removal")


class SilentSerial(FakeSerial):
    def __init__(self, port: str, baud_rate: int, **kwargs: object) -> None:
        super().__init__(port, baud_rate, **kwargs)
        self._chunks.clear()


class SerialWorkerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def test_available_ports_exposes_device_and_description(self) -> None:
        ports = [SimpleNamespace(device="COM7", description="USB Serial Device")]
        with patch(
            "pendulum_workbench.infrastructure.serial_client.list_ports.comports",
            return_value=ports,
        ):
            available = SerialTelemetryClient.available_ports()

        self.assertEqual(available[0].device, "COM7")
        self.assertEqual(available[0].description, "USB Serial Device")

    def test_worker_sends_only_read_only_queries_and_captures_all_received_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            serial_instances: list[FakeSerial] = []

            def serial_factory(port: str, baud_rate: int, **kwargs: object) -> FakeSerial:
                connection = FakeSerial(port, baud_rate, **kwargs)
                serial_instances.append(connection)
                return connection

            worker = SerialWorker(
                "COM_TEST",
                capture_root=Path(temporary_directory),
                serial_factory=serial_factory,
                startup_delay_s=0.0,
            )
            received_lines = []
            capture_paths = []
            worker.line_received.connect(received_lines.append)
            worker.connection_opened.connect(capture_paths.append)
            worker.start()

            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and len(received_lines) < 2:
                self.qt_app.processEvents()
                time.sleep(0.005)
            worker.requestInterruption()
            worker.wait(1000)
            self.qt_app.processEvents()

            self.assertFalse(worker.isRunning())
            self.assertEqual(
                [line.payload for line in received_lines],
                [
                    b"live time=1.000 angle_deg=2.000 position_mm=3.000",
                    b"status: run=0 homed=1 sine=0 parametric=0 parametric_test=0 "
                    b"closed_loop=0 fault=0 upper=0 lower=1",
                    b"live time=1.020 angle=unavailable position_mm=3.000",
                    b"partial",
                ],
            )
            self.assertTrue(received_lines[-1].partial)
            self.assertTrue(serial_instances[0].closed)
            self.assertEqual(serial_instances[0].writes[:2], [b"live 1\r\n", b"status\r\n"])
            self.assertTrue(all(write not in (b"home\r\n", b"stop\r\n") for write in serial_instances[0].writes))
            self.assertEqual(len(capture_paths), 1)

            capture_file = Path(capture_paths[0])
            records = [json.loads(line) for line in capture_file.read_text().splitlines()]
            self.assertEqual(len(records), 4)
            self.assertEqual(records[-1]["partial"], True)
            self.assertEqual(
                base64.b64decode(records[-1]["raw_bytes_base64"]), b"partial"
            )
            self.assertTrue(all(record["received_at_utc"] for record in records))

    def test_real_mode_delivers_status_and_disconnects_without_hardware(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        client = SerialTelemetryClient(
            capture_root=Path(temporary_directory.name),
            serial_factory=FakeSerial,
            startup_delay_s=0.0,
        )
        controller = WorkbenchController(serial_client=client)
        self.addCleanup(controller.shutdown)
        events = []
        controller.state.event_added.connect(events.append)
        controller.select_data_source(DataSourceMode.REAL)
        controller.connect_real("COM_FAKE")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            snapshot = controller.state.snapshot
            if (
                snapshot.samples_received
                and snapshot.hardware_status.homed is not None
                and any("Malformed serial input" in event.message for event in events)
            ):
                break
            time.sleep(0.005)

        snapshot = controller.state.snapshot
        self.assertEqual(snapshot.connection, ConnectionState.CONNECTED)
        self.assertEqual(snapshot.data_source, DataSourceMode.REAL)
        self.assertEqual(snapshot.samples_received, 1)
        self.assertEqual(snapshot.latest_sample.source, "real")
        self.assertEqual(snapshot.motion, MotionState.READY)
        self.assertTrue(snapshot.hardware_status.homed)
        self.assertFalse(snapshot.hardware_status.upper_limit)
        self.assertTrue(snapshot.hardware_status.lower_limit)
        self.assertTrue(snapshot.hardware_status.fault_known)
        self.assertIsNotNone(snapshot.last_valid_telemetry_at)
        self.assertTrue(Path(snapshot.raw_capture_path).exists())
        self.assertTrue(
            any(
                event.severity == EventSeverity.WARNING
                and "angle=unavailable" in event.message
                for event in events
            )
        )

        controller.disconnect_real()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if controller.state.snapshot.connection == ConnectionState.DISCONNECTED:
                break
            time.sleep(0.005)
        self.qt_app.processEvents()

        self.assertEqual(controller.state.snapshot.connection, ConnectionState.DISCONNECTED)

    def test_serial_exception_is_exposed_as_connection_error(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        client = SerialTelemetryClient(
            capture_root=Path(temporary_directory.name),
            serial_factory=DroppedSerial,
            startup_delay_s=0.0,
        )
        controller = WorkbenchController(serial_client=client)
        self.addCleanup(controller.shutdown)
        events = []
        controller.state.event_added.connect(events.append)
        controller.select_data_source(DataSourceMode.REAL)
        controller.connect_real("COM_DROP")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if controller.state.snapshot.connection == ConnectionState.ERROR:
                break
            time.sleep(0.005)

        self.assertEqual(controller.state.snapshot.connection, ConnectionState.ERROR)
        self.assertTrue(
            any(
                event.severity == EventSeverity.ERROR
                and "simulated cable removal" in event.message
                for event in events
            )
        )

    def test_open_but_silent_port_is_marked_stale(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        client = SerialTelemetryClient(
            capture_root=Path(temporary_directory.name),
            serial_factory=SilentSerial,
            stale_after_s=0.05,
            startup_delay_s=0.0,
        )
        controller = WorkbenchController(serial_client=client)
        self.addCleanup(controller.shutdown)
        events = []
        controller.state.event_added.connect(events.append)
        controller.select_data_source(DataSourceMode.REAL)
        controller.connect_real("COM_SILENT")

        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if controller.state.snapshot.telemetry_stale:
                break
            time.sleep(0.005)

        self.assertEqual(controller.state.snapshot.connection, ConnectionState.CONNECTED)
        self.assertTrue(controller.state.snapshot.telemetry_stale)
        self.assertTrue(
            any(
                event.severity == EventSeverity.WARNING
                and "No valid telemetry" in event.message
                for event in events
            )
        )
        controller.disconnect_real()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if controller.state.snapshot.connection == ConnectionState.DISCONNECTED:
                break
            time.sleep(0.005)


if __name__ == "__main__":
    unittest.main()
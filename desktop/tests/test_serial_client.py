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


class SequenceSerial(FakeSerial):
    def __init__(self, port: str, baud_rate: int, **kwargs: object) -> None:
        super().__init__(port, baud_rate, **kwargs)
        self._chunks = [
            b"telemetry sample_index=100 time_s=3.000 angle_deg=1.5 position_mm=2.0\r\n",
            b"telemetry sample_index=103 time_s=3.060 angle_deg=1.6 position_mm=2.1\r\n",
        ]


class OutOfOrderSerial(FakeSerial):
    def __init__(self, port: str, baud_rate: int, **kwargs: object) -> None:
        super().__init__(port, baud_rate, **kwargs)
        self._chunks = [
            b"telemetry sample_index=5 time_s=1.000 angle_deg=1.0 position_mm=2.0\r\n",
            b"telemetry sample_index=4 time_s=1.020 angle_deg=1.1 position_mm=2.1\r\n",
        ]


class SensorRecoverySerial(FakeSerial):
    def __init__(self, port: str, baud_rate: int, **kwargs: object) -> None:
        super().__init__(port, baud_rate, **kwargs)
        self._chunks = [
            b"telemetry sample_index=1 time_s=1.000 angle_deg=NaN position_mm=0.0\r\n"
            b"telemetry sample_index=2 time_s=1.020 angle_deg=NaN position_mm=0.0\r\n"
            b"telemetry sample_index=3 time_s=1.040 angle_deg=NaN position_mm=0.0\r\n"
            b"telemetry sample_index=4 time_s=1.060 angle_deg=0.0 position_mm=0.0\r\n",
        ]


class StructuredEventSerial(FakeSerial):
    def __init__(self, port: str, baud_rate: int, **kwargs: object) -> None:
        super().__init__(port, baud_rate, **kwargs)
        self._chunks = [
            b"event: type=limit_state_changed upper=1 lower=0\r\n"
            b"diag: irq=10 phase=idle\r\n"
            b"RX DEBUG len=4 bytes=68 65 6C 70\r\n"
            b"event: type=angle_sensor_unavailable failures=3 hal_error=2\r\n"
            b"telemetry sample_index=50 time_s=2.000 angle_deg=NaN position_mm=2.0\r\n"
            b"event: type=angle_sensor_restored\r\n"
            b"telemetry sample_index=51 time_s=2.020 angle_deg=0.0 position_mm=2.0\r\n"
            b"event: type=fault_appeared code=upper_limit\r\n",
        ]


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

    def test_device_sample_gap_is_reported_without_generating_routine_events(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        client = SerialTelemetryClient(
            capture_root=Path(temporary_directory.name),
            serial_factory=SequenceSerial,
            startup_delay_s=0.0,
        )
        samples = []
        gaps = []
        messages = []
        client.telemetry_received.connect(samples.append)
        client.telemetry_sequence_gap.connect(gaps.append)
        client.device_message_received.connect(messages.append)
        client.connect_to_port("COM_SEQUENCE")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(samples) < 2:
            self.qt_app.processEvents()
            time.sleep(0.005)
        self.qt_app.processEvents()

        self.assertEqual([sample.sample_index for sample in samples], [100, 103])
        self.assertEqual([sample.elapsed_s for sample in samples], [3.0, 3.06])
        self.assertEqual([sample.received_elapsed_s for sample in samples][0] >= 0.0, True)
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0].expected_sample_index, 101)
        self.assertEqual(gaps[0].received_sample_index, 103)
        self.assertEqual(gaps[0].missing_count, 2)
        self.assertEqual(messages, [])
        client.disconnect()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and client.is_running:
            self.qt_app.processEvents()
            time.sleep(0.005)

    def test_out_of_order_device_sample_is_identified(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        client = SerialTelemetryClient(
            capture_root=Path(temporary_directory.name),
            serial_factory=OutOfOrderSerial,
            startup_delay_s=0.0,
        )
        samples = []
        gaps = []
        client.telemetry_received.connect(samples.append)
        client.telemetry_sequence_gap.connect(gaps.append)
        client.connect_to_port("COM_OUT_OF_ORDER")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(samples) < 2:
            self.qt_app.processEvents()
            time.sleep(0.005)

        self.assertEqual([sample.sample_index for sample in samples], [5, 4])
        self.assertEqual(len(gaps), 1)
        self.assertTrue(gaps[0].out_of_order)
        self.assertEqual(gaps[0].missing_count, 0)
        client.disconnect()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and client.is_running:
            self.qt_app.processEvents()
            time.sleep(0.005)

    def test_sensor_availability_is_reported_only_on_transitions(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        client = SerialTelemetryClient(
            capture_root=Path(temporary_directory.name),
            serial_factory=SensorRecoverySerial,
            startup_delay_s=0.0,
        )
        messages = []
        samples = []
        client.device_message_received.connect(messages.append)
        client.telemetry_received.connect(samples.append)
        client.connect_to_port("COM_SENSOR_RECOVERY")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and len(samples) < 4:
            self.qt_app.processEvents()
            time.sleep(0.005)
        self.qt_app.processEvents()

        self.assertEqual(len(samples), 4)
        self.assertEqual(messages.count("angle sensor unavailable"), 1)
        self.assertEqual(messages.count("angle sensor communication restored"), 1)
        client.disconnect()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and client.is_running:
            self.qt_app.processEvents()
            time.sleep(0.005)

    def test_structured_events_reach_operator_log_but_diagnostics_do_not(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        client = SerialTelemetryClient(
            capture_root=Path(temporary_directory.name),
            serial_factory=StructuredEventSerial,
            startup_delay_s=0.0,
        )
        controller = WorkbenchController(serial_client=client)
        self.addCleanup(controller.shutdown)
        events = []
        controller.state.event_added.connect(events.append)
        controller.select_data_source(DataSourceMode.REAL)
        controller.connect_real("COM_EVENTS")

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if (
                controller.state.snapshot.hardware_status.upper_limit is True
                and controller.state.snapshot.motion == MotionState.FAULT
            ):
                break
            time.sleep(0.005)

        self.assertTrue(controller.state.snapshot.hardware_status.upper_limit)
        self.assertEqual(controller.state.snapshot.motion, MotionState.FAULT)
        messages = [event.message for event in events]
        self.assertTrue(any("Limit state changed" in message for message in messages))
        self.assertTrue(any("Firmware fault appeared" in message for message in messages))
        self.assertFalse(any("telemetry sample_index" in message for message in messages))
        self.assertFalse(any("diag:" in message or "irq=10" in message for message in messages))
        self.assertFalse(any("RX DEBUG" in message for message in messages))
        self.assertEqual(
            messages.count(
                "AS5600 angle sensor communication is unavailable; BIT will fail until a fresh check passes."
            ),
            1,
        )
        self.assertEqual(
            messages.count("AS5600 communication restored; rerun System Check to validate the sensor."),
            1,
        )
        controller.disconnect_real()
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and controller.state.snapshot.connection != ConnectionState.DISCONNECTED:
            self.qt_app.processEvents()
            time.sleep(0.005)

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
            while time.monotonic() < deadline and (
                len(received_lines) < 2
                or not serial_instances
                or b"live 1\r\n" not in serial_instances[0].writes
            ):
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
            self.assertEqual(serial_instances[0].writes, [b"live 1\r\n"])
            self.assertFalse(any(write == b"status\r\n" for write in serial_instances[0].writes))
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

    def test_worker_does_not_periodically_request_status(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            devices: list[SilentSerial] = []

            def serial_factory(port: str, baud_rate: int, **kwargs: object) -> SilentSerial:
                device = SilentSerial(port, baud_rate, **kwargs)
                devices.append(device)
                return device

            worker = SerialWorker(
                "COM_NO_POLL",
                capture_root=Path(temporary_directory),
                serial_factory=serial_factory,
                startup_delay_s=0.0,
            )
            worker.start()
            deadline = time.monotonic() + 1.1
            while time.monotonic() < deadline:
                self.qt_app.processEvents()
                time.sleep(0.005)
            worker.requestInterruption()
            worker.wait(1000)
            self.qt_app.processEvents()

            self.assertEqual(devices[0].writes, [b"live 1\r\n"])

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
                and any("angle sensor unavailable" in event.message for event in events)
            ):
                break
            time.sleep(0.005)

        snapshot = controller.state.snapshot
        self.assertEqual(snapshot.connection, ConnectionState.CONNECTED)
        self.assertEqual(snapshot.data_source, DataSourceMode.REAL)
        self.assertEqual(snapshot.samples_received, 2)
        self.assertEqual(snapshot.latest_sample.source, "real")
        self.assertIsNone(snapshot.latest_sample.angle_deg)
        self.assertEqual(snapshot.motion, MotionState.READY)
        self.assertTrue(snapshot.hardware_status.homed)
        self.assertFalse(snapshot.hardware_status.upper_limit)
        self.assertTrue(snapshot.hardware_status.lower_limit)
        self.assertTrue(snapshot.hardware_status.fault_known)
        self.assertIsNotNone(snapshot.last_valid_telemetry_at)
        self.assertTrue(Path(snapshot.raw_capture_path).exists())
        self.assertTrue(
            any(
                event.severity == EventSeverity.ERROR
                and "angle sensor unavailable" in event.message
                for event in events
            )
        )
        self.assertFalse(any("Hardware status updated" in event.message for event in events))
        self.assertFalse(any("maintenance response received: status" in event.message.lower() for event in events))

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
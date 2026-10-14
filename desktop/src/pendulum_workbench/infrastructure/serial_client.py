import logging
import queue
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import serial
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from serial.tools import list_ports

from pendulum_workbench.domain.models import HardwareStatus, TelemetrySample
from pendulum_workbench.infrastructure.raw_capture import RawSerialCapture, default_capture_root
from pendulum_workbench.infrastructure.telemetry_parser import (
    ParsedLineKind,
    TelemetryParser,
)
from pendulum_workbench.infrastructure.firmware_protocol import (
    DeviceEvent,
    FirmwareProtocolParser,
    MaintenanceResponse,
)

SERIAL_BAUD_RATE = 115200
SERIAL_READ_TIMEOUT_S = 0.02
SERIAL_STARTUP_DELAY_S = 2.0
TELEMETRY_STALE_AFTER_S = 1.5
_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SerialPortInfo:
    device: str
    description: str


@dataclass(frozen=True, slots=True)
class ReceivedSerialLine:
    payload: bytes
    received_at: datetime
    received_elapsed_s: float
    rx_line_index: int
    terminator: bytes = b""
    partial: bool = False


@dataclass(frozen=True, slots=True)
class FramedLine:
    payload: bytes
    terminator: bytes
    partial: bool = False


@dataclass(frozen=True, slots=True)
class TelemetrySequenceGap:
    expected_sample_index: int
    received_sample_index: int
    missing_count: int
    out_of_order: bool = False


class SerialLineFramer:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[FramedLine]:
        self._buffer.extend(data)
        return self._extract(final=False)

    def finish(self) -> list[FramedLine]:
        return self._extract(final=True)

    def _extract(self, *, final: bool) -> list[FramedLine]:
        lines: list[FramedLine] = []
        while True:
            delimiter_index = next(
                (index for index, value in enumerate(self._buffer) if value in (10, 13)),
                None,
            )
            if delimiter_index is None:
                break
            delimiter = self._buffer[delimiter_index]
            if delimiter == 13 and delimiter_index + 1 == len(self._buffer) and not final:
                break
            if delimiter == 13 and self._buffer[delimiter_index + 1:delimiter_index + 2] == b"\n":
                terminator = b"\r\n"
                terminator_length = 2
            else:
                terminator = bytes((delimiter,))
                terminator_length = 1
            payload = bytes(self._buffer[:delimiter_index])
            del self._buffer[:delimiter_index + terminator_length]
            lines.append(FramedLine(payload, terminator))

        if final and self._buffer:
            lines.append(FramedLine(bytes(self._buffer), b"", partial=True))
            self._buffer.clear()
        return lines


class SerialWorker(QThread):
    connection_opened = Signal(str)
    connection_failed = Signal(str)
    connection_closed = Signal(str)
    line_received = Signal(object)
    command_written = Signal(str)

    def __init__(
        self,
        port: str,
        baud_rate: int = SERIAL_BAUD_RATE,
        *,
        capture_root: Path | None = None,
        serial_factory: Callable[..., object] | None = None,
        startup_delay_s: float = SERIAL_STARTUP_DELAY_S,
    ) -> None:
        super().__init__()
        if startup_delay_s < 0:
            raise ValueError("startup_delay_s cannot be negative")
        self.port = port
        self.baud_rate = baud_rate
        self._capture_root = capture_root or default_capture_root()
        self._serial_factory = serial_factory or serial.Serial
        self._startup_delay_s = startup_delay_s
        self._command_queue: queue.Queue[tuple[str, str]] = queue.Queue()
        self._write_receipt_lock = threading.Lock()
        self._last_written_request_id: str | None = None
        self._response_receipt_lock = threading.Lock()
        self._maintenance_response_receipts: dict[int, MaintenanceResponse] = {}
        self._manual_heartbeat_lock = threading.Lock()
        self._manual_heartbeat_direction: str | None = None
        self._manual_heartbeat_revision = 0

    def enqueue_command(self, request_id: str, command: str) -> None:
        if not command or "\r" in command or "\n" in command:
            raise ValueError("serial command must be a single non-empty line")
        self._command_queue.put((request_id, command))

    def command_was_written(self, request_id: str) -> bool:
        with self._write_receipt_lock:
            return self._last_written_request_id == request_id

    def clear_write_receipt(self, request_id: str) -> None:
        with self._write_receipt_lock:
            if self._last_written_request_id == request_id:
                self._last_written_request_id = None

    def take_maintenance_response(self, request_id: int) -> MaintenanceResponse | None:
        with self._response_receipt_lock:
            return self._maintenance_response_receipts.pop(request_id, None)

    def has_maintenance_response(self, request_id: int) -> bool:
        with self._response_receipt_lock:
            return request_id in self._maintenance_response_receipts

    def clear_maintenance_response(self, request_id: int) -> None:
        with self._response_receipt_lock:
            self._maintenance_response_receipts.pop(request_id, None)

    def set_manual_heartbeat(self, direction: str | None) -> None:
        if direction not in (None, "up", "down"):
            raise ValueError("manual heartbeat direction must be 'up', 'down', or None")
        with self._manual_heartbeat_lock:
            self._manual_heartbeat_direction = direction
            self._manual_heartbeat_revision += 1

    def run(self) -> None:
        connection = None
        capture = None
        opened = False
        close_reason = "Serial connection closed"
        framer = SerialLineFramer()
        session_start = 0.0
        heartbeat_revision = 0
        try:
            connection = self._serial_factory(
                self.port,
                self.baud_rate,
                timeout=SERIAL_READ_TIMEOUT_S,
                write_timeout=0.5,
            )
            capture = RawSerialCapture(self._capture_root)
            session_start = time.monotonic()
            opened = True
            self.connection_opened.emit(str(capture.path))

            telemetry_started = False

            while not self.isInterruptionRequested():
                chunk = connection.read(256)
                if chunk:
                    lines = framer.feed(chunk)
                    self._record_lines(lines, capture, session_start)

                if time.monotonic() - session_start >= self._startup_delay_s:
                    while True:
                        try:
                            request_id, command = self._command_queue.get_nowait()
                        except queue.Empty:
                            break
                        payload = f"{command}\r\n".encode("ascii")
                        if connection.write(payload) != len(payload):
                            raise IOError("serial command was only partially written")
                        with self._write_receipt_lock:
                            self._last_written_request_id = request_id
                        self.command_written.emit(request_id)

                now = time.monotonic()
                if (
                    not telemetry_started
                    and now - session_start >= self._startup_delay_s
                ):
                    if self.isInterruptionRequested():
                        break
                    connection.write(b"live 1\r\n")
                    telemetry_started = True

                with self._manual_heartbeat_lock:
                    if self._manual_heartbeat_revision != heartbeat_revision:
                        direction = self._manual_heartbeat_direction
                        heartbeat_revision = self._manual_heartbeat_revision
                        if direction is not None:
                            command = f"manual_keepalive_{direction}"
                            payload = f"maint 0 {command}\r\n".encode("ascii")
                            if connection.write(payload) != len(payload):
                                raise IOError("serial heartbeat was only partially written")

        except Exception as error:
            close_reason = f"Serial connection to {self.port} failed: {error}"
            self.connection_failed.emit(close_reason)
        finally:
            final_lines = framer.finish()
            if final_lines and capture is not None:
                self._record_lines(final_lines, capture, session_start)
            if connection is not None:
                try:
                    connection.close()
                except Exception as error:
                    if opened:
                        close_reason = f"Serial port close failed: {error}"
                        self.connection_failed.emit(close_reason)
            if capture is not None:
                capture.close()
            if opened:
                self.connection_closed.emit(close_reason)

    def _record_lines(
        self,
        lines: list[FramedLine],
        capture: RawSerialCapture,
        session_start: float,
    ) -> None:
        for line in lines:
            received_at = datetime.now(timezone.utc)
            received_elapsed_s = time.monotonic() - session_start
            rx_line_index = capture.write_line(
                line.payload,
                line.terminator,
                received_elapsed_s,
                received_at,
                partial=line.partial,
            )
            response_text = line.payload.decode("ascii", errors="replace")
            if response_text.startswith("> "):
                response_text = response_text[2:].lstrip()
            response = FirmwareProtocolParser.parse_maintenance_response(response_text)
            if response is not None:
                with self._response_receipt_lock:
                    self._maintenance_response_receipts[response.request_id] = response
                    if len(self._maintenance_response_receipts) > 32:
                        oldest_id = next(iter(self._maintenance_response_receipts))
                        self._maintenance_response_receipts.pop(oldest_id, None)
            self.line_received.emit(
                ReceivedSerialLine(
                    payload=line.payload,
                    received_at=received_at,
                    received_elapsed_s=received_elapsed_s,
                    rx_line_index=rx_line_index,
                    terminator=line.terminator,
                    partial=line.partial,
                )
            )


class SerialTelemetryClient(QObject):
    connection_opened = Signal(str)
    connection_failed = Signal(str)
    connection_closed = Signal(str)
    telemetry_received = Signal(object)
    raw_line_received = Signal(object)
    hardware_status_received = Signal(object)
    device_message_received = Signal(str)
    malformed_line_received = Signal(str)
    telemetry_stale_changed = Signal(bool)
    telemetry_sequence_gap = Signal(object)
    command_written = Signal(str)
    maintenance_response_received = Signal(object)
    device_event_received = Signal(object)
    diagnostic_received = Signal(str)
    controller_diagnostic_received = Signal(object)

    def __init__(
        self,
        capture_root: Path | None = None,
        serial_factory: Callable[..., object] | None = None,
        stale_after_s: float = TELEMETRY_STALE_AFTER_S,
        startup_delay_s: float = SERIAL_STARTUP_DELAY_S,
    ) -> None:
        super().__init__()
        if stale_after_s <= 0:
            raise ValueError("stale_after_s must be positive")
        if startup_delay_s < 0:
            raise ValueError("startup_delay_s cannot be negative")
        self._capture_root = capture_root
        self._serial_factory = serial_factory
        self._stale_after_s = stale_after_s
        self._startup_delay_s = startup_delay_s
        self._parser = TelemetryParser()
        self._worker: SerialWorker | None = None
        self._sample_index = 0
        self._last_device_sample_index: int | None = None
        self._angle_sensor_available: bool | None = None
        self._connected_monotonic: float | None = None
        self._last_valid_monotonic: float | None = None
        self._is_stale = False
        self._watchdog = QTimer(self)
        self._watchdog.setInterval(250)
        self._watchdog.timeout.connect(self._check_telemetry_freshness)

    @staticmethod
    def available_ports() -> list[SerialPortInfo]:
        return [
            SerialPortInfo(port.device, port.description or port.device)
            for port in list_ports.comports()
        ]

    @property
    def is_running(self) -> bool:
        return self._worker is not None and self._worker.isRunning()

    def connect_to_port(self, port: str) -> None:
        if self.is_running:
            raise RuntimeError("A serial connection is already active")
        if not port:
            raise ValueError("A serial port must be selected")

        self._sample_index = 0
        self._last_device_sample_index = None
        self._angle_sensor_available = None
        self._connected_monotonic = None
        self._last_valid_monotonic = None
        self._set_stale(False)
        worker = SerialWorker(
            port,
            capture_root=self._capture_root,
            serial_factory=self._serial_factory,
            startup_delay_s=self._startup_delay_s,
        )
        worker.connection_opened.connect(self._on_connection_opened)
        worker.connection_failed.connect(self.connection_failed)
        worker.connection_closed.connect(self._on_connection_closed)
        worker.line_received.connect(self._on_line_received)
        worker.command_written.connect(self.command_written)
        self._worker = worker
        worker.start()

    def send_maintenance_command(self, request_id: str, command: str) -> None:
        worker = self._worker
        if worker is None or not worker.isRunning():
            raise RuntimeError("STM32 serial connection is not running")
        worker.enqueue_command(request_id, command)

    def command_was_written(self, request_id: str) -> bool:
        worker = self._worker
        return worker is not None and worker.command_was_written(request_id)

    def clear_write_receipt(self, request_id: str) -> None:
        worker = self._worker
        if worker is not None:
            worker.clear_write_receipt(request_id)

    def take_maintenance_response(self, request_id: int) -> MaintenanceResponse | None:
        worker = self._worker
        return worker.take_maintenance_response(request_id) if worker is not None else None

    def has_maintenance_response(self, request_id: int) -> bool:
        worker = self._worker
        return worker is not None and worker.has_maintenance_response(request_id)

    def clear_maintenance_response(self, request_id: int) -> None:
        worker = self._worker
        if worker is not None:
            worker.clear_maintenance_response(request_id)

    def set_manual_heartbeat(self, direction: str | None) -> None:
        worker = self._worker
        if direction is None:
            if worker is not None:
                worker.set_manual_heartbeat(None)
            return
        if worker is None or not worker.isRunning():
            raise RuntimeError("STM32 serial connection is not running")
        worker.set_manual_heartbeat(direction)

    def disconnect(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
        elif self._worker is None:
            self.connection_closed.emit("Serial connection is already closed")

    def shutdown(self, wait_ms: int = 1000) -> None:
        if self._worker is not None and self._worker.isRunning():
            self._worker.requestInterruption()
            self._worker.wait(wait_ms)

    def _on_connection_opened(self, capture_path: str) -> None:
        self._connected_monotonic = time.monotonic() + self._startup_delay_s
        self._watchdog.start()
        self.connection_opened.emit(capture_path)

    def _on_connection_closed(self, reason: str) -> None:
        self._watchdog.stop()
        self._connected_monotonic = None
        self._last_valid_monotonic = None
        self.connection_closed.emit(reason)
        self._set_stale(False)

    def _on_line_received(self, received_line: ReceivedSerialLine) -> None:
        self.raw_line_received.emit(received_line)
        if received_line.partial:
            self.malformed_line_received.emit(
                "Unterminated partial serial line preserved in raw capture."
            )
            return
        text = received_line.payload.decode("ascii", errors="replace")
        if text.startswith("> "):
            text = text[2:].lstrip()
        maintenance_response = FirmwareProtocolParser.parse_maintenance_response(text)
        if maintenance_response is not None:
            self.maintenance_response_received.emit(maintenance_response)
            return
        device_event = FirmwareProtocolParser.parse_device_event(text)
        if device_event is not None:
            if device_event.name == "angle_sensor_unavailable":
                self._angle_sensor_available = False
            elif device_event.name == "angle_sensor_restored":
                self._angle_sensor_available = True
            self.device_event_received.emit(device_event)
            return
        diagnostic = FirmwareProtocolParser.parse_diagnostic(text)
        if diagnostic is not None:
            _LOGGER.debug("Firmware diagnostic: %s", diagnostic.text)
            self.diagnostic_received.emit(diagnostic.text)
            return
        result = self._parser.parse_line(
            text,
            sample_index=self._sample_index + 1,
            elapsed_s=received_line.received_elapsed_s,
            received_at=received_line.received_at,
            rx_line_index=received_line.rx_line_index,
        )
        if result.kind == ParsedLineKind.TELEMETRY and result.telemetry is not None:
            self._sample_index += 1
            angle_available = result.telemetry.angle_deg is not None
            if angle_available != self._angle_sensor_available:
                if not angle_available:
                    self.device_message_received.emit("angle sensor unavailable")
                elif self._angle_sensor_available is False:
                    self.device_message_received.emit("angle sensor communication restored")
                self._angle_sensor_available = angle_available
            device_index = result.device_sample_index
            if device_index is not None:
                previous_index = self._last_device_sample_index
                if previous_index is not None and device_index != previous_index + 1:
                    missing_count = max(0, device_index - previous_index - 1)
                    self.telemetry_sequence_gap.emit(
                        TelemetrySequenceGap(
                            expected_sample_index=previous_index + 1,
                            received_sample_index=device_index,
                            missing_count=missing_count,
                            out_of_order=device_index <= previous_index,
                        )
                    )
                self._last_device_sample_index = device_index
            self._last_valid_monotonic = time.monotonic()
            self._set_stale(False)
            self.telemetry_received.emit(result.telemetry)
        elif result.kind == ParsedLineKind.HARDWARE_STATUS and result.hardware_status is not None:
            self.hardware_status_received.emit(result.hardware_status)
        elif result.kind == ParsedLineKind.CONTROLLER_DIAGNOSTIC:
            self.controller_diagnostic_received.emit(result)
            self.diagnostic_received.emit(text)
        elif result.kind == ParsedLineKind.MALFORMED:
            self.malformed_line_received.emit(result.message)
        elif result.message:
            self.device_message_received.emit(result.message)

    def _check_telemetry_freshness(self) -> None:
        anchor = self._last_valid_monotonic or self._connected_monotonic
        if anchor is None:
            return
        stale = time.monotonic() - anchor > self._stale_after_s
        self._set_stale(stale)

    def _set_stale(self, stale: bool) -> None:
        if stale == self._is_stale:
            return
        self._is_stale = stale
        self.telemetry_stale_changed.emit(stale)
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

SERIAL_BAUD_RATE = 115200
SERIAL_READ_TIMEOUT_S = 0.1
SERIAL_STARTUP_DELAY_S = 2.0
STATUS_POLL_INTERVAL_S = 1.0
TELEMETRY_STALE_AFTER_S = 1.5


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

    def run(self) -> None:
        connection = None
        capture = None
        opened = False
        close_reason = "Serial connection closed"
        framer = SerialLineFramer()
        session_start = 0.0
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

            queries_started = False
            next_status_poll = 0.0

            while not self.isInterruptionRequested():
                chunk = connection.read(256)
                if chunk:
                    self._record_lines(framer.feed(chunk), capture, session_start)

                now = time.monotonic()
                if not queries_started and now - session_start >= self._startup_delay_s:
                    if self.isInterruptionRequested():
                        break
                    connection.write(b"live 1\r\n")
                    connection.write(b"status\r\n")
                    queries_started = True
                    next_status_poll = now + STATUS_POLL_INTERVAL_S
                elif queries_started and now >= next_status_poll:
                    connection.write(b"status\r\n")
                    next_status_poll = now + STATUS_POLL_INTERVAL_S

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
        self._worker = worker
        worker.start()

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
        result = self._parser.parse_line(
            text,
            sample_index=self._sample_index + 1,
            elapsed_s=received_line.received_elapsed_s,
            received_at=received_line.received_at,
            rx_line_index=received_line.rx_line_index,
        )
        if result.kind == ParsedLineKind.TELEMETRY and result.telemetry is not None:
            self._sample_index += 1
            self._last_valid_monotonic = time.monotonic()
            self._set_stale(False)
            self.telemetry_received.emit(result.telemetry)
        elif result.kind == ParsedLineKind.HARDWARE_STATUS and result.hardware_status is not None:
            self.hardware_status_received.emit(result.hardware_status)
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
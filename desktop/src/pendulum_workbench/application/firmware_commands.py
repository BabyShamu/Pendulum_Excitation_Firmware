from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QObject, QTimer, Signal

from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.models import ApplicationSnapshot, ConnectionState, EventSeverity
from pendulum_workbench.infrastructure.firmware_protocol import MaintenanceResponse
from pendulum_workbench.infrastructure.serial_client import SerialTelemetryClient


class FirmwareCommand(str, Enum):
    STATUS = "status"
    SWITCHES = "switches"
    ANGLE = "angle"
    HOME = "home"
    ZERO = "zero"
    MANUAL_UP = "manual_up"
    MANUAL_DOWN = "manual_down"
    MANUAL_STOP = "manual_stop"


@dataclass(frozen=True, slots=True)
class FirmwareCommandFailure:
    command: FirmwareCommand
    message: str


class FirmwareCommandService(QObject):
    command_completed = Signal(object)
    command_failed = Signal(object)

    _RESPONSE_TIMEOUT_MS = 3500
    _DISPATCH_TIMEOUT_MS = 5000
    _HOME_TIMEOUT_MS = 65000
    _MANUAL_COMMAND_TIMEOUT_MS = 500

    def __init__(
        self,
        state: ApplicationStateModel,
        serial_client: SerialTelemetryClient,
    ) -> None:
        super().__init__()
        self.state = state
        self.serial_client = serial_client
        self._next_request_id = 1
        self._active_request_id: int | None = None
        self._active_command: FirmwareCommand | None = None
        self._response_timeout_ms = self._RESPONSE_TIMEOUT_MS
        self._waiting_for_write = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self.serial_client.command_written.connect(self._on_command_written)
        self.serial_client.maintenance_response_received.connect(self._on_response)
        self.state.snapshot_changed.connect(self._on_snapshot_changed)

    @property
    def is_busy(self) -> bool:
        return self._active_request_id is not None

    def request_status(self) -> int:
        return self._request(FirmwareCommand.STATUS)

    def request_switches(self) -> int:
        return self._request(FirmwareCommand.SWITCHES)

    def request_angle(self) -> int:
        return self._request(FirmwareCommand.ANGLE)

    def home(self) -> int:
        return self._request(FirmwareCommand.HOME, timeout_ms=self._HOME_TIMEOUT_MS)

    def set_angle_zero(self) -> int:
        return self._request(FirmwareCommand.ZERO)

    @staticmethod
    def _is_routine_success(command: FirmwareCommand) -> bool:
        return command in (
            FirmwareCommand.STATUS,
            FirmwareCommand.SWITCHES,
            FirmwareCommand.ANGLE,
        )

    def manual_start_up(self) -> int:
        return self._request(FirmwareCommand.MANUAL_UP, timeout_ms=self._MANUAL_COMMAND_TIMEOUT_MS)

    def manual_start_down(self) -> int:
        return self._request(FirmwareCommand.MANUAL_DOWN, timeout_ms=self._MANUAL_COMMAND_TIMEOUT_MS)

    def manual_stop(self) -> int:
        return self._request(FirmwareCommand.MANUAL_STOP, timeout_ms=self._MANUAL_COMMAND_TIMEOUT_MS)

    def _request(self, command: FirmwareCommand, *, timeout_ms: int | None = None) -> int:
        if self.state.snapshot.connection != ConnectionState.CONNECTED:
            raise RuntimeError("Connect to the STM32 before sending a maintenance command")
        if self.is_busy:
            raise RuntimeError("Another STM32 command is still awaiting a response")

        request_id = self._next_request_id
        self._next_request_id += 1
        self._active_request_id = request_id
        self._active_command = command
        self._response_timeout_ms = timeout_ms or self._RESPONSE_TIMEOUT_MS
        self._waiting_for_write = True
        self._timer.start(self._DISPATCH_TIMEOUT_MS)
        try:
            self.serial_client.send_maintenance_command(
                str(request_id),
                f"maint {request_id} {command.value}",
            )
        except (RuntimeError, ValueError, OSError) as error:
            self._fail(str(error))
            raise
        if not self._is_routine_success(command):
            self.state.report_event(
                EventSeverity.INFO,
                f"Maintenance command requested: {command.value} (request {request_id}).",
            )
        return request_id

    def _on_command_written(self, request_id: str) -> None:
        if self._active_request_id is None or request_id != str(self._active_request_id):
            return
        if not self._is_routine_success(self._active_command):
            self.state.report_event(
                EventSeverity.INFO,
                f"Maintenance command sent: {self._active_command.value} (request {request_id}).",
            )
        self._waiting_for_write = False
        self._timer.start(self._response_timeout_ms)

    def _on_response(self, response: MaintenanceResponse) -> None:
        if self._active_request_id != response.request_id:
            return
        if self._active_command is None or response.response_type != self._active_command.value:
            self._fail(
                f"Unexpected maintenance response type {response.response_type!r} "
                f"for request {response.request_id}."
            )
            return
        if not response.ok:
            self._fail(
                response.fields.get("error", "firmware rejected the maintenance command")
            )
            return

        command = self._active_command
        self._clear_active()
        if not self._is_routine_success(command):
            self.state.report_event(
                EventSeverity.INFO,
                f"Maintenance response received: {command.value} (request {response.request_id}).",
            )
        self.command_completed.emit((command, response))

    def _on_timeout(self) -> None:
        if self._active_command is None:
            return
        phase = "response" if not self._waiting_for_write else "dispatch"
        self._fail(f"Timed out waiting for maintenance command {phase}: {self._active_command.value}.")

    def _on_snapshot_changed(self, snapshot: ApplicationSnapshot) -> None:
        if self.is_busy and snapshot.connection != ConnectionState.CONNECTED:
            self._fail("STM32 connection ended while a maintenance command was active")

    def _fail(self, message: str) -> None:
        command = self._active_command
        if command is None:
            return
        self._clear_active()
        self.state.report_event(EventSeverity.ERROR, f"Maintenance command {command.value} failed: {message}")
        self.command_failed.emit(FirmwareCommandFailure(command, message))

    def _clear_active(self) -> None:
        self._timer.stop()
        self._active_request_id = None
        self._active_command = None
        self._waiting_for_write = False
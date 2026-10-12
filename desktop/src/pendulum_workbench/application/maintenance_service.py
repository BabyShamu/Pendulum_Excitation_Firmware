import math
from datetime import datetime, timezone
from typing import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from pendulum_workbench.application.firmware_commands import (
    FirmwareCommand,
    FirmwareCommandFailure,
    FirmwareCommandService,
)
from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.maintenance import (
    BitResultState,
    CheckName,
    CheckResult,
    CheckState,
    MaintenanceSnapshot,
    ManualMotionState,
    ReadinessState,
)
from pendulum_workbench.domain.models import (
    ApplicationSnapshot,
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    HardwareStatus,
    HomeStatus,
    MotionState,
    RecordingState,
    TelemetrySample,
)
from pendulum_workbench.infrastructure.firmware_protocol import DeviceEvent, MaintenanceResponse
from pendulum_workbench.infrastructure.serial_client import SerialTelemetryClient


_CHECK_ORDER = (
    CheckName.COMMUNICATION,
    CheckName.UPPER_LIMIT,
    CheckName.LOWER_LIMIT,
    CheckName.ANGLE_SENSOR,
    CheckName.FIRMWARE_STATUS,
    CheckName.FAULT_STATE,
)
MANUAL_KEEPALIVE_INTERVAL_MS = 100


class MaintenanceService(QObject):
    snapshot_changed = Signal(object)

    def __init__(
        self,
        state: ApplicationStateModel,
        serial_client: SerialTelemetryClient,
        commands: FirmwareCommandService,
    ) -> None:
        super().__init__()
        self.state = state
        self.serial_client = serial_client
        self.commands = commands
        self._check_results = {name: CheckResult(name) for name in _CHECK_ORDER}
        self._bit_state = BitResultState.NOT_RUN
        self._bit_passed = False
        self._last_check_at: datetime | None = None
        self._active_operation: str | None = None
        self._operation_message = ""
        self._operation_fault = False
        self._homing_failure_latched = False
        self._sensor_failure_latched = False
        self._current_angle_deg: float | None = None
        self._travel_span_steps: int | None = None
        self._position_steps: int | None = None
        self._steps_per_mm: float | None = None
        self._position_mm: float | None = None
        self._bit_stage: str | None = None
        self._status_sync_pending = False
        self._status_sync_deferred = False
        self._manual_direction: str | None = None
        self._manual_stop_requested = False
        self._manual_failure_latched = False
        self._manual_communication_failed = False
        self._manual_motion_state = ManualMotionState.IDLE
        self._manual_motion_message = "Manual pivot is idle."
        self._manual_keepalive = QTimer(self)
        self._manual_keepalive.timeout.connect(self._send_manual_keepalive)
        self._previous_connection = state.snapshot.connection
        self._previous_source = state.snapshot.data_source
        self._last_snapshot = self._build_snapshot()

        state.snapshot_changed.connect(self._on_application_snapshot)
        state.telemetry_received.connect(self._on_telemetry_sample)
        serial_client.device_message_received.connect(self._on_device_message)
        serial_client.device_event_received.connect(self._on_device_event)
        commands.command_completed.connect(self._on_command_completed)
        commands.command_failed.connect(self._on_command_failed)

    @property
    def snapshot(self) -> MaintenanceSnapshot:
        return self._build_snapshot()

    @property
    def is_busy(self) -> bool:
        return self._active_operation is not None or self.commands.is_busy

    @property
    def is_homing(self) -> bool:
        return self._active_operation == "homing"

    @property
    def is_manual_motion_active(self) -> bool:
        return self._manual_direction is not None

    def start_manual_motion(self, direction: str) -> None:
        if direction not in ("up", "down"):
            raise ValueError("Manual direction must be 'up' or 'down'")
        self._require_connected()
        snapshot = self.state.snapshot
        if self.is_busy or self._manual_direction is not None:
            raise RuntimeError("Another maintenance operation or manual direction is active")
        if not self._bit_passed:
            raise RuntimeError("Run the current-session System Check before manual positioning")
        if snapshot.hardware_status.homed is not True:
            raise RuntimeError("Home the pivot before manual positioning")
        if snapshot.motion in (MotionState.RUNNING, MotionState.HOMING, MotionState.STOPPING):
            raise RuntimeError("Another device motion mode is active")
        if snapshot.hardware_status.fault_known and snapshot.hardware_status.fault:
            raise RuntimeError("Manual motion is blocked by the firmware fault state")
        if snapshot.recording in (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        ):
            raise RuntimeError("Stop experiment recording before manual positioning")
        limit_active = (
            snapshot.hardware_status.upper_limit
            if direction == "up"
            else snapshot.hardware_status.lower_limit
        )
        if limit_active is True:
            raise RuntimeError(f"The {direction} travel limit is already active")

        self._manual_direction = direction
        self._manual_stop_requested = False
        self._manual_failure_latched = False
        self._manual_communication_failed = False
        self.serial_client.set_manual_heartbeat(None)
        self._manual_motion_state = (
            ManualMotionState.STARTING_UP if direction == "up" else ManualMotionState.STARTING_DOWN
        )
        self._manual_motion_message = f"Requesting manual motion {direction}."
        self._active_operation = "manual_start"
        self.state.report_event(EventSeverity.WARNING, f"Manual pivot motion requested {direction}.")
        self._emit_snapshot()
        self._request(
            self.commands.manual_start_up if direction == "up" else self.commands.manual_start_down
        )

    def stop_manual_motion(self, reason: str = "button_release") -> None:
        if self._manual_direction is None:
            return
        self._stop_manual_keepalive()
        self._manual_stop_requested = True
        self._manual_motion_state = ManualMotionState.STOPPING
        self._manual_motion_message = f"Stopping manual motion: {reason.replace('_', ' ')}."
        if self._active_operation in ("manual_start", "manual"):
            self._active_operation = "manual_stop_pending"
        self._emit_snapshot()
        if not self.commands.is_busy:
            self._send_manual_stop()

    def run_system_check(self) -> None:
        self._require_connected()
        if self.is_busy:
            raise RuntimeError("A maintenance operation is already active")

        self._operation_fault = False
        self._bit_passed = False
        self._bit_state = BitResultState.CHECKING
        self._last_check_at = None
        self._check_results = {name: CheckResult(name) for name in _CHECK_ORDER}
        self._set_check(CheckName.COMMUNICATION, CheckState.CHECKING, "Waiting for firmware response")
        self._set_check(CheckName.FIRMWARE_STATUS, CheckState.CHECKING, "Waiting for status response")
        self._set_check(CheckName.FAULT_STATE, CheckState.CHECKING, "Waiting for fault state")
        self._active_operation = "system_check"
        self._operation_message = "Checking STM32 communication and firmware status..."
        self.state.report_event(EventSeverity.INFO, "Maintenance system check started.")
        self._emit_snapshot()
        self._request(self.commands.request_status)

    def start_homing(self) -> None:
        snapshot = self.state.snapshot
        self._require_connected()
        if self.is_busy:
            raise RuntimeError("A maintenance operation is already active")
        if snapshot.home_status == HomeStatus.HOMING or snapshot.motion == MotionState.HOMING:
            raise RuntimeError("Homing is already active")
        if snapshot.motion in (MotionState.RUNNING, MotionState.STOPPING):
            raise RuntimeError("Stop active device motion before homing")
        if snapshot.recording in (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        ):
            raise RuntimeError("Stop the active experiment recording before homing")
        if snapshot.hardware_status.fault_known and snapshot.hardware_status.fault:
            raise RuntimeError("Homing is blocked by the firmware fault state")

        self._operation_fault = False
        self._homing_failure_latched = False
        self._active_operation = "homing"
        self._operation_message = "Homing in progress..."
        self.state.set_home_status(HomeStatus.HOMING)
        self.state.set_motion(MotionState.HOMING)
        self.state.report_event(EventSeverity.WARNING, "Maintenance homing started; the pivot will move.")
        self._emit_snapshot()
        self._request(self.commands.home)

    def set_angle_zero(self) -> None:
        self._require_connected()
        if self.is_busy:
            raise RuntimeError("A maintenance operation is already active")
        snapshot = self.state.snapshot
        if snapshot.motion in (MotionState.RUNNING, MotionState.HOMING, MotionState.STOPPING):
            raise RuntimeError("Angle zero is unavailable while device motion is active")
        if snapshot.recording in (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        ):
            raise RuntimeError("Stop the active experiment recording before changing angle zero")
        self._active_operation = "zero"
        self._operation_message = "Setting angle reference..."
        self.state.report_event(
            EventSeverity.WARNING,
            "Angle sensor zero requested; the measurement reference will change.",
        )
        self._emit_snapshot()
        self._request(self.commands.set_angle_zero)

    def _request(self, command: Callable[[], int]) -> None:
        try:
            command()
        except (RuntimeError, ValueError, OSError) as error:
            if self._active_operation == "system_check":
                self._fail_system_check(str(error))
            elif self._active_operation == "homing":
                self._fail_homing(str(error))
            elif self._active_operation == "zero":
                self._finish_zero(False, str(error))
            elif self._active_operation in (
                "manual_start",
                "manual",
                "manual_stop",
                "manual_stop_pending",
            ):
                self._fail_manual_motion(str(error))
            else:
                raise

    def _on_command_completed(self, result: tuple[FirmwareCommand, MaintenanceResponse]) -> None:
        command, response = result
        if (
            self._active_operation is None
            and self._status_sync_pending
            and command == FirmwareCommand.STATUS
        ):
            self._status_sync_pending = False
            self._apply_status_fields(response.fields)
            self._emit_snapshot()
            return
        if command == FirmwareCommand.MANUAL_STOP:
            self._finish_manual_stop(
                response.fields.get("active") == "0",
                self._manual_motion_message,
            )
            return
        if self._active_operation == "system_check":
            self._handle_system_check_response(command, response)
        elif self._active_operation == "homing":
            self._handle_homing_response(command, response)
        elif self._active_operation == "zero":
            self._handle_zero_response(command, response)
        elif self._active_operation == "manual_start":
            expected = FirmwareCommand.MANUAL_UP if self._manual_direction == "up" else FirmwareCommand.MANUAL_DOWN
            if command != expected or response.fields.get("active") != "1":
                self._fail_manual_motion("Firmware did not confirm the requested direction")
            elif self._manual_stop_requested:
                self._send_manual_stop()
            else:
                self._active_operation = "manual"
                self._manual_motion_state = (
                    ManualMotionState.MOVING_UP
                    if self._manual_direction == "up"
                    else ManualMotionState.MOVING_DOWN
                )
                self._manual_motion_message = f"Moving {self._manual_direction}; release the button to stop."
                self.state.set_motion(MotionState.RUNNING)
                self._manual_keepalive.start(MANUAL_KEEPALIVE_INTERVAL_MS)
                self._emit_snapshot()
        elif self._active_operation == "manual_stop_pending":
            if command in (
                FirmwareCommand.MANUAL_UP,
                FirmwareCommand.MANUAL_DOWN,
            ):
                self._send_manual_stop()
        if self._status_sync_deferred and self._active_operation is None:
            self._request_status_sync()

    def _handle_system_check_response(
        self,
        command: FirmwareCommand,
        response: MaintenanceResponse,
    ) -> None:
        fields = response.fields
        if command == FirmwareCommand.STATUS and self._bit_stage is None:
            self._set_check(CheckName.COMMUNICATION, CheckState.PASS, "Correlated firmware response received")
            required = ("homed", "fault", "position_steps", "steps_per_mm")
            missing = [name for name in required if name not in fields]
            if "upper_limit" not in fields and "upper" not in fields:
                missing.append("upper_limit")
            if "lower_limit" not in fields and "lower" not in fields:
                missing.append("lower_limit")
            if "travel_span_steps" not in fields and "span_steps" not in fields:
                missing.append("travel_span_steps")
            if "active_mode" not in fields and not any(
                name in fields for name in ("run", "sine", "parametric", "closed_loop")
            ):
                missing.append("active_mode")
            if missing:
                self._set_check(
                    CheckName.FIRMWARE_STATUS,
                    CheckState.FAIL,
                    f"Status response missing: {', '.join(missing)}",
                )
                self._set_check(CheckName.FAULT_STATE, CheckState.FAIL, "Fault state was not reported")
                self._finish_system_check()
                return

            self._apply_status_fields(fields)
            self._set_check(CheckName.FIRMWARE_STATUS, CheckState.PASS, "Valid firmware status received")
            fault_code = self._as_int(fields.get("fault"))
            if fault_code is None:
                self._set_check(CheckName.FAULT_STATE, CheckState.FAIL, "Fault code was not valid")
            elif fault_code == 0:
                self._set_check(CheckName.FAULT_STATE, CheckState.PASS, "Firmware reports no blocking fault")
            else:
                self._set_check(CheckName.FAULT_STATE, CheckState.FAIL, f"Firmware fault code {fault_code}")

            self._bit_stage = "switches"
            self._set_check(CheckName.UPPER_LIMIT, CheckState.CHECKING, "Reading upper input state")
            self._set_check(CheckName.LOWER_LIMIT, CheckState.CHECKING, "Reading lower input state")
            self._operation_message = "Reading both limit inputs..."
            self._emit_snapshot()
            self._request(self.commands.request_switches)
            return

        if command == FirmwareCommand.SWITCHES:
            upper = self._as_bit(fields.get("upper"))
            lower = self._as_bit(fields.get("lower"))
            if upper is None or lower is None:
                detail = "Firmware did not return valid upper and lower input states"
                self._set_check(CheckName.UPPER_LIMIT, CheckState.FAIL, detail)
                self._set_check(CheckName.LOWER_LIMIT, CheckState.FAIL, detail)
                self._finish_system_check()
                return
            self.state.set_hardware_status(
                HardwareStatus(upper_limit=upper, lower_limit=lower, updated_at=datetime.now(timezone.utc))
            )
            self._set_check(
                CheckName.UPPER_LIMIT,
                CheckState.PASS,
                f"Input readable: {'ACTIVE' if upper else 'INACTIVE'}; electrical actuation not tested",
            )
            self._set_check(
                CheckName.LOWER_LIMIT,
                CheckState.PASS,
                f"Input readable: {'ACTIVE' if lower else 'INACTIVE'}; electrical actuation not tested",
            )
            self._bit_stage = "angle"
            self._set_check(CheckName.ANGLE_SENSOR, CheckState.CHECKING, "Reading AS5600 angle")
            self._operation_message = "Checking angular sensor..."
            self._emit_snapshot()
            self._request(self.commands.request_angle)
            return

        if command == FirmwareCommand.ANGLE:
            degrees = self._as_float(fields.get("degrees"))
            if degrees is None:
                self._sensor_failure_latched = True
                self._set_check(CheckName.ANGLE_SENSOR, CheckState.FAIL, "No valid AS5600 angle was returned")
            else:
                self._sensor_failure_latched = False
                self._current_angle_deg = degrees
                raw_angle = fields.get("raw", "unknown")
                self._set_check(CheckName.ANGLE_SENSOR, CheckState.PASS, f"AS5600 responded; raw={raw_angle}")
            self._finish_system_check()

    def _handle_homing_response(
        self,
        command: FirmwareCommand,
        response: MaintenanceResponse,
    ) -> None:
        if command == FirmwareCommand.HOME:
            fields = response.fields
            self._update_travel_fields(fields)
            if fields.get("homed") != "1" or fields.get("fault") != "0":
                self._fail_homing(fields.get("error", "Firmware did not confirm homing success"))
                return
            self._operation_message = "Home sequence completed; verifying firmware status..."
            self._emit_snapshot()
            self._request(self.commands.request_status)
            return

        if command == FirmwareCommand.STATUS:
            self._apply_status_fields(response.fields)
            if response.fields.get("homed") == "1" and response.fields.get("fault") == "0":
                self.state.set_home_status(HomeStatus.HOMED)
                self.state.set_motion(MotionState.READY)
                self._operation_fault = False
                self._homing_failure_latched = False
                self._active_operation = None
                self._operation_message = "Homing confirmed by firmware."
                self.state.report_event(EventSeverity.INFO, "Homing completed and firmware reports homed=1.")
                self._emit_snapshot()
            else:
                self._fail_homing("Post-home firmware status did not confirm homed=1 and fault=0")

    def _handle_zero_response(
        self,
        command: FirmwareCommand,
        response: MaintenanceResponse,
    ) -> None:
        if command == FirmwareCommand.ZERO:
            if response.fields.get("zeroed") != "1":
                self._finish_zero(False, response.fields.get("error", "Firmware did not confirm angle zero"))
                return
            self._operation_message = "Zero applied; verifying a subsequent AS5600 reading..."
            self._emit_snapshot()
            self._request(self.commands.request_angle)
            return

        if command == FirmwareCommand.ANGLE:
            degrees = self._as_float(response.fields.get("degrees"))
            if degrees is None:
                self._finish_zero(False, "No valid AS5600 reading followed the zero command")
                return
            self._current_angle_deg = degrees
            self._finish_zero(True, f"Angle reference set; subsequent reading is {degrees:+.2f} deg")
            self._request_status_sync()

    def _handle_command_failed(self, failure: FirmwareCommandFailure) -> None:
        if self._active_operation == "system_check":
            self._fail_system_check(failure.message, command=failure.command)
        elif self._active_operation == "homing":
            self._fail_homing(failure.message)
        elif self._active_operation == "zero":
            self._finish_zero(False, failure.message)

    def _on_command_failed(self, failure: FirmwareCommandFailure) -> None:
        if self._status_sync_pending and failure.command == FirmwareCommand.STATUS:
            self._status_sync_pending = False
            self.state.report_event(
                EventSeverity.WARNING,
                f"Could not synchronize STM32 status: {failure.message}",
            )
            self._emit_snapshot()
            return
        if failure.command == FirmwareCommand.MANUAL_STOP:
            self._fail_manual_motion(failure.message, retry_stop=False)
            return
        if self._active_operation in (
            "manual_start",
            "manual",
            "manual_stop",
            "manual_stop_pending",
        ):
            self._fail_manual_motion(failure.message)
            if self._status_sync_deferred and self._active_operation is None:
                self._request_status_sync()
            return
        self._handle_command_failed(failure)
        if self._status_sync_deferred and self._active_operation is None:
            self._request_status_sync()

    def _fail_system_check(
        self,
        message: str,
        *,
        command: FirmwareCommand | None = None,
    ) -> None:
        message = self._user_facing_error(message)
        if self.state.snapshot.connection != ConnectionState.CONNECTED:
            self._finish_incomplete_system_check(message)
            return
        if command in (None, FirmwareCommand.STATUS):
            for name in (CheckName.COMMUNICATION, CheckName.FIRMWARE_STATUS, CheckName.FAULT_STATE):
                if self._check_results[name].state == CheckState.CHECKING:
                    self._set_check(name, CheckState.FAIL, message)
        elif command == FirmwareCommand.SWITCHES:
            for name in (CheckName.UPPER_LIMIT, CheckName.LOWER_LIMIT):
                if self._check_results[name].state == CheckState.CHECKING:
                    self._set_check(name, CheckState.FAIL, message)
        elif command == FirmwareCommand.ANGLE:
            self._set_check(CheckName.ANGLE_SENSOR, CheckState.FAIL, message)
        self._finish_system_check()

    def _finish_incomplete_system_check(self, message: str) -> None:
        for name, result in tuple(self._check_results.items()):
            if result.state == CheckState.CHECKING:
                self._set_check(name, CheckState.WARNING, "Session ended before this check completed")
        self._bit_stage = None
        self._bit_passed = False
        self._bit_state = BitResultState.INCOMPLETE
        self._operation_fault = False
        self._last_check_at = datetime.now(timezone.utc)
        self._active_operation = None
        self._operation_message = f"System check incomplete: {message}"
        self.state.report_event(EventSeverity.WARNING, self._operation_message)
        self._emit_snapshot()

    def _finish_system_check(self) -> None:
        self._bit_stage = None
        passed = all(result.state == CheckState.PASS for result in self._check_results.values())
        self._bit_state = BitResultState.PASSED if passed else BitResultState.FAILED
        self._bit_passed = passed
        self._operation_fault = not passed
        self._last_check_at = datetime.now(timezone.utc)
        self._active_operation = None
        self._operation_message = "System check passed." if passed else "System check failed; review failed rows."
        severity = EventSeverity.INFO if passed else EventSeverity.ERROR
        self.state.report_event(severity, self._operation_message)
        self._emit_snapshot()

    def _fail_homing(self, message: str) -> None:
        message = self._user_facing_error(message)
        self._active_operation = None
        self._operation_fault = True
        self._homing_failure_latched = True
        self._operation_message = f"HOMING FAILED: {message}"
        self.state.set_home_status(HomeStatus.HOMING_FAILED)
        self.state.set_motion(MotionState.FAULT)
        self.state.report_event(EventSeverity.ERROR, self._operation_message)
        self._emit_snapshot()

    def _finish_zero(self, success: bool, message: str) -> None:
        message = self._user_facing_error(message)
        self._active_operation = None
        self._operation_message = message
        severity = EventSeverity.INFO if success else EventSeverity.ERROR
        self.state.report_event(severity, message)
        self._emit_snapshot()
        if success:
            self._request_status_sync()

    def _send_manual_keepalive(self) -> None:
        if self._manual_direction is None or self._manual_stop_requested:
            return
        try:
            self.serial_client.set_manual_heartbeat(self._manual_direction)
        except (RuntimeError, ValueError, OSError) as error:
            self._fail_manual_motion(str(error))

    def _stop_manual_keepalive(self) -> None:
        self._manual_keepalive.stop()
        self.serial_client.set_manual_heartbeat(None)

    def _send_manual_stop(self) -> None:
        if self._manual_direction is None:
            return
        self._stop_manual_keepalive()
        if self.commands.is_busy:
            self._active_operation = "manual_stop_pending"
            return
        self._active_operation = "manual_stop"
        try:
            self.commands.manual_stop()
        except (RuntimeError, ValueError, OSError) as error:
            self._fail_manual_motion(str(error), retry_stop=False)

    def _finish_manual_stop(self, confirmed: bool, message: str) -> None:
        self._stop_manual_keepalive()
        self._manual_direction = None
        self._manual_stop_requested = False
        self._active_operation = None
        if self._manual_failure_latched:
            self._manual_motion_state = ManualMotionState.FAULT
            severity = EventSeverity.ERROR
        elif self._manual_communication_failed or not confirmed:
            self._manual_motion_state = ManualMotionState.COMMUNICATION_STOP
            severity = EventSeverity.ERROR
        elif self._manual_motion_state in (
            ManualMotionState.DEADMAN_STOP,
            ManualMotionState.SAFETY_STOP,
        ):
            severity = EventSeverity.WARNING
        else:
            self._manual_motion_state = ManualMotionState.IDLE
            self._manual_motion_message = message or "Manual pivot is idle."
            severity = EventSeverity.INFO
        self._manual_failure_latched = False
        self._manual_communication_failed = False
        self.state.set_motion(
            MotionState.READY if self.state.snapshot.hardware_status.homed else MotionState.IDLE
        )
        self.state.report_event(severity, self._manual_motion_message)
        self._emit_snapshot()
        self._request_status_sync()

    def _fail_manual_motion(self, message: str, *, retry_stop: bool = True) -> None:
        self._stop_manual_keepalive()
        self._manual_communication_failed = True
        self._manual_motion_state = ManualMotionState.COMMUNICATION_STOP
        self._manual_motion_message = (
            f"Manual motion communication/safety stop: {self._user_facing_error(message)}"
        )
        self.state.report_event(EventSeverity.ERROR, self._manual_motion_message)
        self._emit_snapshot()
        if (
            retry_stop
            and self.state.snapshot.connection == ConnectionState.CONNECTED
            and self._manual_direction is not None
            and not self.commands.is_busy
        ):
            self._manual_stop_requested = True
            self._send_manual_stop()
        elif self.state.snapshot.connection != ConnectionState.CONNECTED or not retry_stop:
            self._manual_direction = None
            self._manual_stop_requested = False
            self._active_operation = None

    def _apply_status_fields(self, fields: dict[str, str]) -> None:
        self._update_travel_fields(fields)
        homed = self._as_bit(fields.get("homed"))
        upper = self._as_bit(fields.get("upper_limit", fields.get("upper")))
        lower = self._as_bit(fields.get("lower_limit", fields.get("lower")))
        fault_code = self._as_int(fields.get("fault"))
        if homed is None or upper is None or lower is None or fault_code is None:
            return
        hardware_status = HardwareStatus(
            homed=homed,
            upper_limit=upper,
            lower_limit=lower,
            active_motion_mode=self._mode_from_fields(fields),
            fault=f"Firmware fault {fault_code}" if fault_code else None,
            fault_known=True,
            updated_at=datetime.now(timezone.utc),
            travel_span_steps=self._as_int(
                fields.get("travel_span_steps", fields.get("span_steps"))
            ),
            position_steps=self._as_int(fields.get("position_steps")),
            steps_per_mm=self._as_float(fields.get("steps_per_mm")),
        )
        self.state.set_hardware_status(hardware_status)
        if fault_code:
            self.state.set_motion(MotionState.FAULT)

    def _update_travel_fields(self, fields: dict[str, str]) -> None:
        self._travel_span_steps = self._as_int(
            fields.get("travel_span_steps", fields.get("span_steps")),
            self._travel_span_steps,
        )
        self._position_steps = self._as_int(fields.get("position_steps"), self._position_steps)
        self._steps_per_mm = self._as_float(fields.get("steps_per_mm"), self._steps_per_mm)
        self._position_mm = self._as_float(fields.get("position_mm"), self._position_mm)
        if (
            "position_mm" not in fields
            and self._position_steps is not None
            and self._steps_per_mm is not None
            and self._steps_per_mm > 0
        ):
            self._position_mm = self._position_steps / self._steps_per_mm

    def _on_device_message(self, message: str) -> None:
        normalized = message.strip().lower()
        if normalized == "angle sensor unavailable":
            self._mark_angle_sensor_unavailable()
            return
        if normalized == "angle sensor communication restored":
            self._mark_angle_sensor_restored()
            return
        if self._active_operation != "homing":
            return
        if normalized.startswith("homing:"):
            self._operation_message = message.strip()
            self._emit_snapshot()

    def _on_device_event(self, event: DeviceEvent) -> None:
        if event.name == "angle_sensor_unavailable":
            self._mark_angle_sensor_unavailable()
            return
        if event.name == "angle_sensor_restored":
            self._mark_angle_sensor_restored()
            return
        if event.name == "fault_appeared":
            self._operation_fault = True
            self._operation_message = f"Firmware fault appeared: {event.fields.get('code', 'unknown')}"
            if self._manual_direction is not None:
                self._manual_failure_latched = True
                self._manual_motion_message = self._operation_message
                self.stop_manual_motion("blocking_fault")
            self._emit_snapshot()
            return
        if event.name == "fault_cleared":
            self._operation_fault = False
            self._operation_message = "Firmware fault cleared; readiness still requires current BIT/home state."
            self._emit_snapshot()
            return
        if event.name == "manual_motion_started":
            direction = event.fields.get("direction")
            if direction in ("up", "down"):
                self._manual_direction = direction
                self._manual_motion_state = (
                    ManualMotionState.MOVING_UP
                    if direction == "up"
                    else ManualMotionState.MOVING_DOWN
                )
                self._manual_motion_message = f"Moving {direction}; release the button to stop."
                self.state.set_motion(MotionState.RUNNING)
                self._emit_snapshot()
            return
        if event.name != "manual_motion_stopped":
            return

        direction = event.fields.get("direction", self._manual_direction or "unknown")
        reason = event.fields.get("reason", "unknown")
        self._stop_manual_keepalive()
        self._manual_direction = None
        self._manual_stop_requested = False
        self._active_operation = None
        if self._manual_failure_latched:
            self._manual_motion_state = ManualMotionState.FAULT
        elif self._manual_communication_failed:
            self._manual_motion_state = ManualMotionState.COMMUNICATION_STOP
        elif reason == "deadman_timeout":
            self._manual_motion_state = ManualMotionState.DEADMAN_STOP
        elif reason == "interlock":
            self._manual_motion_state = ManualMotionState.SAFETY_STOP
        elif reason in ("upper_limit", "lower_limit"):
            self._manual_motion_state = ManualMotionState.SAFETY_STOP
        else:
            self._manual_motion_state = ManualMotionState.IDLE
        self._manual_motion_message = f"Manual motion {direction} stopped: {reason.replace('_', ' ')}."
        self.state.set_motion(
            MotionState.READY if self.state.snapshot.hardware_status.homed else MotionState.IDLE
        )
        self._emit_snapshot()
        self._request_status_sync()

    def _mark_angle_sensor_unavailable(self) -> None:
        self._sensor_failure_latched = True
        self._bit_passed = False
        self._bit_state = BitResultState.FAILED
        self._operation_fault = True
        self._set_check(
            CheckName.ANGLE_SENSOR,
            CheckState.FAIL,
            "AS5600 communication failed; run System Check again after recovery.",
        )
        if self._manual_direction is not None:
            self.stop_manual_motion("sensor_unavailable")
        self._emit_snapshot()

    def _mark_angle_sensor_restored(self) -> None:
        self._sensor_failure_latched = False
        self._bit_passed = False
        self._operation_fault = False
        self._set_check(
            CheckName.ANGLE_SENSOR,
            CheckState.WARNING,
            "Communication restored; rerun System Check to validate the AS5600.",
        )
        self._operation_message = "AS5600 communication restored; System Check required."
        self._emit_snapshot()

    def _on_telemetry_sample(self, sample: TelemetrySample) -> None:
        if (
            self.state.snapshot.data_source != DataSourceMode.REAL
            or self.state.snapshot.connection != ConnectionState.CONNECTED
        ):
            return
        changed = False
        if sample.angle_deg is not None:
            self._current_angle_deg = sample.angle_deg
            changed = True
        if sample.position_mm is not None:
            self._position_mm = sample.position_mm
            changed = True
        if changed:
            self._emit_snapshot()

    def _on_application_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        if (
            self._manual_direction is not None
            and snapshot.connection == ConnectionState.CONNECTED
            and snapshot.motion == MotionState.FAULT
        ):
            self.stop_manual_motion("blocking_fault")
        connection_changed = snapshot.connection != self._previous_connection
        source_changed = snapshot.data_source != self._previous_source
        new_session = (
            snapshot.connection == ConnectionState.CONNECTING
            and self._previous_connection != ConnectionState.CONNECTING
        )
        if source_changed or new_session or (
            connection_changed and snapshot.connection != ConnectionState.CONNECTED
        ):
            self._invalidate_session(
                preserve_incomplete=(
                    not source_changed
                    and not new_session
                    and self._bit_state == BitResultState.INCOMPLETE
                )
            )
        self._previous_connection = snapshot.connection
        self._previous_source = snapshot.data_source
        self._emit_snapshot()
        if connection_changed and snapshot.connection == ConnectionState.CONNECTED:
            self._request_status_sync()

    def _request_status_sync(self) -> None:
        if self.state.snapshot.connection != ConnectionState.CONNECTED:
            return
        if self.commands.is_busy:
            self._status_sync_deferred = True
            return
        if self._status_sync_pending:
            return
        self._status_sync_deferred = False
        self._status_sync_pending = True
        try:
            self.commands.request_status()
        except (RuntimeError, ValueError, OSError) as error:
            self._status_sync_pending = False
            self.state.report_event(
                EventSeverity.WARNING,
                f"Could not request STM32 status sync: {error}",
            )

    def _invalidate_session(self, *, preserve_incomplete: bool = False) -> None:
        manual_was_active = self._manual_direction is not None
        self._stop_manual_keepalive()
        if manual_was_active:
            self._manual_direction = None
            self._manual_stop_requested = False
            self._active_operation = None
            self._manual_communication_failed = True
            self._manual_motion_state = ManualMotionState.COMMUNICATION_STOP
            self._manual_motion_message = (
                "Serial session ended during manual motion; firmware dead-man timeout must stop the pivot."
            )
            self.state.report_event(EventSeverity.ERROR, self._manual_motion_message)
        self._bit_passed = False
        if not preserve_incomplete:
            self._bit_state = BitResultState.NOT_RUN
            self._check_results = {name: CheckResult(name) for name in _CHECK_ORDER}
            self._last_check_at = None
        self._operation_fault = False
        self._homing_failure_latched = False
        self._sensor_failure_latched = False
        self._travel_span_steps = None
        self._position_steps = None
        self._steps_per_mm = None
        self._position_mm = None
        self._current_angle_deg = None
        if self._active_operation == "system_check":
            self._active_operation = None
            self._operation_message = "System check invalidated by connection/source change."
        elif self._active_operation == "homing":
            self._active_operation = None
            self._operation_fault = True
            self._homing_failure_latched = True
            self._operation_message = "HOMING FAILED: STM32 session ended before confirmation."
            self.state.set_home_status(HomeStatus.HOMING_FAILED)
            self.state.set_motion(MotionState.FAULT)
        elif self._active_operation == "zero":
            self._active_operation = None
            self._operation_message = "Angle zero ended because the STM32 session changed."

    def _set_check(self, name: CheckName, state: CheckState, detail: str) -> None:
        self._check_results[name] = CheckResult(name, state, detail)

    def _build_snapshot(self) -> MaintenanceSnapshot:
        application = self.state.snapshot
        if application.connection != ConnectionState.CONNECTED:
            readiness = ReadinessState.NOT_CONNECTED
        elif application.hardware_status.fault_known and application.hardware_status.fault:
            readiness = ReadinessState.FAULT
        elif self._operation_fault or self._homing_failure_latched or self._sensor_failure_latched:
            readiness = ReadinessState.FAULT
        elif not self._bit_passed:
            readiness = ReadinessState.CHECK_REQUIRED
        elif application.hardware_status.homed is True:
            readiness = ReadinessState.SYSTEM_READY
        else:
            readiness = ReadinessState.BIT_PASSED_HOME_REQUIRED
        return MaintenanceSnapshot(
            readiness=readiness,
            bit_state=self._bit_state,
            checks=tuple(self._check_results[name] for name in _CHECK_ORDER),
            latest_check_at=self._last_check_at,
            operation=self._active_operation,
            operation_message=self._operation_message,
            current_angle_deg=self._current_angle_deg,
            travel_span_steps=self._travel_span_steps,
            position_steps=self._position_steps,
            steps_per_mm=self._steps_per_mm,
            position_mm=self._position_mm,
            manual_motion=self._manual_motion_state,
            manual_motion_message=self._manual_motion_message,
        )

    def _emit_snapshot(self) -> None:
        snapshot = self._build_snapshot()
        if snapshot != self._last_snapshot:
            self._last_snapshot = snapshot
            self.snapshot_changed.emit(snapshot)

    def _require_connected(self) -> None:
        if self.state.snapshot.connection != ConnectionState.CONNECTED:
            raise RuntimeError("Connect to the STM32 before using Maintenance")

    @staticmethod
    def _user_facing_error(message: str) -> str:
        if message == "as5600_unavailable":
            return "AS5600 did not respond"
        return message.replace("_", " ")

    @staticmethod
    def _mode_from_fields(fields: dict[str, str]) -> str:
        active_mode = fields.get("active_mode")
        active_modes = {
            "idle": "Idle",
            "manual_up": "Manual motion up",
            "manual_down": "Manual motion down",
            "manual_stepping": "Manual stepping",
            "parametric_closed_loop": "Parametric closed loop",
            "parametric_test": "Parametric test (no motion)",
        }
        if active_mode in active_modes:
            return active_modes[active_mode]
        return (
            "Parametric closed loop" if fields.get("closed_loop") == "1" else
            "Sine" if fields.get("sine") == "1" else
            "Parametric" if fields.get("parametric") == "1" else
            "Parametric test (no motion)" if fields.get("parametric_test") == "1" else
            "Manual stepping" if fields.get("run") == "1" else
            "Idle"
        )

    @staticmethod
    def _as_bit(value: str | None) -> bool | None:
        return None if value not in ("0", "1") else value == "1"

    @staticmethod
    def _as_int(value: str | None, default: int | None = None) -> int | None:
        try:
            return int(value) if value is not None else default
        except ValueError:
            return default

    @staticmethod
    def _as_float(value: str | None, default: float | None = None) -> float | None:
        try:
            number = float(value) if value is not None else default
        except ValueError:
            return default
        return number if number is not None and math.isfinite(number) else default
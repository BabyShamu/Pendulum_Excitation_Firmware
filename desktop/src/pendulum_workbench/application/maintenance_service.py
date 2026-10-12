import math
from datetime import datetime, timezone
from typing import Callable

from PySide6.QtCore import QObject, Signal

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
from pendulum_workbench.infrastructure.firmware_protocol import MaintenanceResponse
from pendulum_workbench.infrastructure.serial_client import SerialTelemetryClient


_CHECK_ORDER = (
    CheckName.COMMUNICATION,
    CheckName.UPPER_LIMIT,
    CheckName.LOWER_LIMIT,
    CheckName.ANGLE_SENSOR,
    CheckName.FIRMWARE_STATUS,
    CheckName.FAULT_STATE,
)


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
        self._current_angle_deg: float | None = None
        self._travel_span_steps: int | None = None
        self._position_steps: int | None = None
        self._steps_per_mm: float | None = None
        self._position_mm: float | None = None
        self._bit_stage: str | None = None
        self._previous_connection = state.snapshot.connection
        self._previous_source = state.snapshot.data_source
        self._last_snapshot = self._build_snapshot()

        state.snapshot_changed.connect(self._on_application_snapshot)
        state.telemetry_received.connect(self._on_telemetry_sample)
        serial_client.device_message_received.connect(self._on_device_message)
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
            else:
                raise

    def _on_command_completed(self, result: tuple[FirmwareCommand, MaintenanceResponse]) -> None:
        command, response = result
        if self._active_operation == "system_check":
            self._handle_system_check_response(command, response)
        elif self._active_operation == "homing":
            self._handle_homing_response(command, response)
        elif self._active_operation == "zero":
            self._handle_zero_response(command, response)

    def _handle_system_check_response(
        self,
        command: FirmwareCommand,
        response: MaintenanceResponse,
    ) -> None:
        fields = response.fields
        if command == FirmwareCommand.STATUS and self._bit_stage is None:
            self._set_check(CheckName.COMMUNICATION, CheckState.PASS, "Correlated firmware response received")
            required = ("homed", "upper", "lower", "fault", "span_steps", "position_steps", "steps_per_mm")
            missing = [name for name in required if name not in fields]
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
                self._set_check(CheckName.ANGLE_SENSOR, CheckState.FAIL, "No valid AS5600 angle was returned")
            else:
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

    def _handle_command_failed(self, failure: FirmwareCommandFailure) -> None:
        if self._active_operation == "system_check":
            self._fail_system_check(failure.message, command=failure.command)
        elif self._active_operation == "homing":
            self._fail_homing(failure.message)
        elif self._active_operation == "zero":
            self._finish_zero(False, failure.message)

    def _on_command_failed(self, failure: FirmwareCommandFailure) -> None:
        self._handle_command_failed(failure)

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

    def _apply_status_fields(self, fields: dict[str, str]) -> None:
        self._update_travel_fields(fields)
        homed = self._as_bit(fields.get("homed"))
        upper = self._as_bit(fields.get("upper"))
        lower = self._as_bit(fields.get("lower"))
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
        )
        self.state.set_hardware_status(hardware_status)
        if fault_code:
            self.state.set_motion(MotionState.FAULT)

        angle = self._as_float(fields.get("angle_deg")) if fields.get("angle_ok") == "1" else None
        if angle is not None:
            self._current_angle_deg = angle
        self._emit_snapshot()

    def _update_travel_fields(self, fields: dict[str, str]) -> None:
        self._travel_span_steps = self._as_int(fields.get("span_steps"), self._travel_span_steps)
        self._position_steps = self._as_int(fields.get("position_steps"), self._position_steps)
        self._steps_per_mm = self._as_float(fields.get("steps_per_mm"), self._steps_per_mm)
        self._position_mm = self._as_float(fields.get("position_mm"), self._position_mm)

    def _on_device_message(self, message: str) -> None:
        if self._active_operation != "homing":
            return
        if message.strip().lower().startswith("homing:"):
            self._operation_message = message.strip()
            self._emit_snapshot()

    def _on_telemetry_sample(self, sample: TelemetrySample) -> None:
        if (
            sample.angle_deg is not None
            and self.state.snapshot.data_source == DataSourceMode.REAL
            and self.state.snapshot.connection == ConnectionState.CONNECTED
        ):
            self._current_angle_deg = sample.angle_deg
            self._emit_snapshot()

    def _on_application_snapshot(self, snapshot: ApplicationSnapshot) -> None:
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

    def _invalidate_session(self, *, preserve_incomplete: bool = False) -> None:
        self._bit_passed = False
        if not preserve_incomplete:
            self._bit_state = BitResultState.NOT_RUN
            self._check_results = {name: CheckResult(name) for name in _CHECK_ORDER}
            self._last_check_at = None
        self._operation_fault = False
        self._homing_failure_latched = False
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
        elif self._operation_fault or self._homing_failure_latched:
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
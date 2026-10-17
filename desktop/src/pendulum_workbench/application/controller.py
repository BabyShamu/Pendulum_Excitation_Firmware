from pathlib import Path

from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal

from pendulum_workbench.application.firmware_commands import FirmwareCommandService
from pendulum_workbench.application.maintenance_service import MaintenanceService
from pendulum_workbench.application.experiment_recording import ExperimentRecordingService
from pendulum_workbench.application.experiment_run import ExperimentRunService
from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    HardwareStatus,
    HomeStatus,
    MotionState,
)
from pendulum_workbench.infrastructure.mock_telemetry import MockTelemetrySource
from pendulum_workbench.infrastructure.serial_client import (
    SerialPortInfo,
    SerialTelemetryClient,
    TelemetrySequenceGap,
)
from pendulum_workbench.infrastructure.firmware_protocol import DeviceEvent
from pendulum_workbench.experiments.repository import ExperimentRepository, LoadedExperiment


class WorkbenchController(QObject):
    experiment_loaded = Signal(object)
    _EXPERIMENT_SHUTDOWN_STOP_TIMEOUT_MS = 76000

    def __init__(
        self,
        state: ApplicationStateModel | None = None,
        mock_source: MockTelemetrySource | None = None,
        serial_client: SerialTelemetryClient | None = None,
        experiment_repository: ExperimentRepository | None = None,
    ) -> None:
        super().__init__()
        self.state = state or ApplicationStateModel()
        self.mock_source = mock_source or MockTelemetrySource()
        self.serial_client = serial_client or SerialTelemetryClient()
        self.experiment_recorder = ExperimentRecordingService(
            self.state,
            self.serial_client,
            experiment_repository,
        )
        self.firmware_commands = FirmwareCommandService(self.state, self.serial_client)
        self.maintenance = MaintenanceService(
            self.state,
            self.serial_client,
            self.firmware_commands,
        )
        self.experiment_run = ExperimentRunService(
            self.state,
            self.experiment_recorder,
            self.firmware_commands,
            self.maintenance,
        )
        self._loaded_experiment: LoadedExperiment | None = None
        self._disconnect_requested = False
        self.mock_source.sample_ready.connect(self.state.publish_sample)
        self.serial_client.connection_opened.connect(self._on_serial_opened)
        self.serial_client.connection_failed.connect(self._on_serial_failed)
        self.serial_client.connection_closed.connect(self._on_serial_closed)
        self.serial_client.telemetry_received.connect(self.state.publish_sample)
        self.serial_client.hardware_status_received.connect(self.state.set_hardware_status)
        self.serial_client.device_message_received.connect(self._on_device_message)
        self.serial_client.device_event_received.connect(self._on_device_event)
        self.serial_client.telemetry_sequence_gap.connect(self._on_telemetry_sequence_gap)
        self.serial_client.malformed_line_received.connect(self._on_malformed_line)
        self.serial_client.telemetry_stale_changed.connect(self._on_telemetry_stale)

    @staticmethod
    def available_ports() -> list[SerialPortInfo]:
        return SerialTelemetryClient.available_ports()

    def select_data_source(self, mode: DataSourceMode | str) -> None:
        mode = self._coerce_data_source_mode(mode)
        if self.state.snapshot.connection in (
            ConnectionState.CONNECTING,
            ConnectionState.CONNECTED,
            ConnectionState.DISCONNECTING,
        ):
            raise RuntimeError("Disconnect the STM32 before changing data source")
        if mode == self.state.snapshot.data_source:
            return
        if mode == DataSourceMode.REAL:
            self.stop_mock()
            self.state.set_data_source(mode)
        elif mode == DataSourceMode.MOCK:
            self.state.set_data_source(mode)
            self.start_mock()
        else:
            if self._loaded_experiment is None:
                raise RuntimeError("Open a saved experiment before selecting saved-data mode")
            self.stop_mock()
            self.state.set_data_source(DataSourceMode.SAVED)
            self.state.set_saved_telemetry(self._loaded_experiment.telemetry)
            self.experiment_loaded.emit(self._loaded_experiment)

    @staticmethod
    def _coerce_data_source_mode(mode: DataSourceMode | str) -> DataSourceMode:
        if isinstance(mode, DataSourceMode):
            return mode
        if isinstance(mode, str):
            try:
                return DataSourceMode(mode)
            except ValueError as error:
                allowed = ", ".join(item.value for item in DataSourceMode)
                raise ValueError(f"Unknown data source {mode!r}; expected one of: {allowed}") from error
        raise TypeError(f"data source must be DataSourceMode or str, got {type(mode).__name__}")

    def start_mock(self) -> None:
        if self.state.snapshot.data_source != DataSourceMode.MOCK:
            return
        if self.mock_source.is_active:
            return
        self.state.set_mock_source_active(True)
        self.state.report_event(
            EventSeverity.INFO,
            "Mock telemetry started; no STM32 connection is active.",
        )
        self.mock_source.start()

    def stop_mock(self) -> None:
        if not self.mock_source.is_active:
            return
        self.mock_source.stop()
        self.state.set_mock_source_active(False)
        self.state.report_event(EventSeverity.WARNING, "Mock telemetry paused.")

    def connect_real(self, port: str) -> None:
        if self.state.snapshot.data_source != DataSourceMode.REAL:
            raise RuntimeError("Select REAL STM32 mode before connecting")
        if self.state.snapshot.connection not in (
            ConnectionState.DISCONNECTED,
            ConnectionState.ERROR,
        ):
            raise RuntimeError("A serial connection is already active")
        self._disconnect_requested = False
        self.state.begin_serial_session(port)
        self.state.report_event(EventSeverity.INFO, f"Connecting to {port} at 115200 baud.")
        try:
            self.serial_client.connect_to_port(port)
        except (RuntimeError, ValueError) as error:
            self.state.set_connection(ConnectionState.ERROR, port)
            self.state.report_event(EventSeverity.ERROR, str(error))

    def disconnect_real(self) -> None:
        if self.experiment_run.is_active:
            self.experiment_run.stop("disconnect_requested")
            self.state.report_event(
                EventSeverity.WARNING,
                "Experiment stop requested; disconnect again after the run is safely finalized.",
            )
            return
        if self.maintenance.is_manual_motion_active:
            self.maintenance.stop_manual_motion("disconnect_requested")
            self.state.report_event(
                EventSeverity.WARNING,
                "Manual pivot stop requested; disconnect again after motion is confirmed stopped.",
            )
            return
        if self.maintenance.is_homing:
            self.state.report_event(
                EventSeverity.WARNING,
                "Wait for homing to finish before disconnecting the STM32.",
            )
            return
        if self.state.snapshot.connection not in (
            ConnectionState.CONNECTING,
            ConnectionState.CONNECTED,
        ):
            return
        self._disconnect_requested = True
        self.state.set_connection(ConnectionState.DISCONNECTING)
        self.state.report_event(EventSeverity.INFO, "Disconnecting serial transport.")
        self.serial_client.disconnect()

    def shutdown(self) -> None:
        if self.experiment_run.is_active:
            self.experiment_run.stop("application_shutdown")
            if self.experiment_run.is_active:
                loop = QEventLoop()

                def quit_when_experiment_stops(_snapshot) -> None:
                    if not self.experiment_run.is_active:
                        loop.quit()

                self.experiment_run.snapshot_changed.connect(quit_when_experiment_stops)
                QTimer.singleShot(self._EXPERIMENT_SHUTDOWN_STOP_TIMEOUT_MS, loop.quit)
                loop.exec()
                self.experiment_run.snapshot_changed.disconnect(quit_when_experiment_stops)
        if self.maintenance.is_manual_motion_active:
            self.maintenance.stop_manual_motion("application_shutdown")
            if self.maintenance.is_manual_motion_active:
                loop = QEventLoop()

                def quit_when_manual_stops(_snapshot) -> None:
                    if not self.maintenance.is_manual_motion_active:
                        loop.quit()

                self.maintenance.snapshot_changed.connect(quit_when_manual_stops)
                QTimer.singleShot(750, loop.quit)
                loop.exec()
                self.maintenance.snapshot_changed.disconnect(quit_when_manual_stops)
                if self.maintenance.is_manual_motion_active:
                    self.state.report_event(
                        EventSeverity.ERROR,
                        "Manual stop was not confirmed before shutdown; firmware dead-man timeout is the fallback.",
                    )
        self.stop_mock()
        self.experiment_recorder.shutdown()
        self.serial_client.shutdown()

    def open_experiment(self, source: Path | str) -> LoadedExperiment:
        if self.experiment_recorder.is_recording:
            raise RuntimeError("Stop the active recording before opening a saved experiment")
        if self.state.snapshot.connection not in (
            ConnectionState.DISCONNECTED,
            ConnectionState.ERROR,
        ):
            raise RuntimeError("Disconnect the STM32 before opening a saved experiment")
        loaded = self.experiment_recorder.load_experiment(source)
        self.stop_mock()
        self._loaded_experiment = loaded
        self.state.set_data_source(DataSourceMode.SAVED)
        self.state.set_saved_telemetry(loaded.telemetry)
        self.state.report_event(
            EventSeverity.INFO,
            f"Opened experiment {loaded.manifest.get('name') or loaded.experiment_id}.",
        )
        self.experiment_loaded.emit(loaded)
        return loaded

    def _on_serial_opened(self, capture_path: str) -> None:
        self.state.set_raw_capture_path(capture_path)
        if self.state.snapshot.connection != ConnectionState.DISCONNECTING:
            self.state.set_connection(
                ConnectionState.CONNECTED,
                self.state.snapshot.real_port,
            )
            self.state.report_event(
                EventSeverity.INFO,
                "STM32 serial connection opened; telemetry enabled and compact status synchronization requested.",
            )

    def _on_serial_failed(self, message: str) -> None:
        self.state.set_connection(ConnectionState.ERROR, self.state.snapshot.real_port)
        self.state.report_event(EventSeverity.ERROR, message)

    def _on_serial_closed(self, reason: str) -> None:
        if self._disconnect_requested:
            self.state.set_connection(ConnectionState.DISCONNECTED)
            self.state.report_event(EventSeverity.INFO, "Serial disconnected.")
        elif self.state.snapshot.connection != ConnectionState.ERROR:
            self.state.set_connection(ConnectionState.DISCONNECTED)
            self.state.report_event(EventSeverity.WARNING, reason)

    def _on_device_message(self, message: str) -> None:
        normalized = message.strip().lower()
        if normalized.startswith("homing failed:"):
            self.state.set_home_status(HomeStatus.HOMING_FAILED)
        elif normalized.startswith("homing:"):
            self.state.set_home_status(HomeStatus.HOMING)
        if (
            normalized.startswith("angle sensor unavailable")
            and self.experiment_run.tolerates_transient_angle_dropout
        ):
            self.state.report_event(
                EventSeverity.WARNING,
                "AS5600 communication degraded: temporarily unavailable during the active experiment; missing angle samples remain unavailable while firmware applies its fault threshold.",
            )
            return
        meaningful_prefixes = (
            "homing failed:",
            "homing calibration:",
            "closed loop stopped:",
            "sine stopped:",
            "stopped:",
            "angle sensor unavailable",
            "angle sensor communication restored",
        )
        if normalized.startswith(meaningful_prefixes) or (
            normalized.startswith("homing:") and "moving to" in normalized
        ):
            severity = EventSeverity.ERROR if normalized.startswith(
                ("homing failed:", "angle sensor unavailable")
            ) else EventSeverity.INFO
            self.state.report_event(severity, message[:180])

    def _on_device_event(self, event: DeviceEvent) -> None:
        fields = event.fields
        messages = {
            "homing_lower_limit_reached": "Homing reached the lower limit.",
            "homing_upper_limit_reached": "Homing reached the upper limit.",
            "homing_completed": "Firmware homing sequence completed; verifying status.",
            "limit_state_changed": (
                f"Limit state changed: upper={fields.get('upper', 'unknown')} "
                f"lower={fields.get('lower', 'unknown')}."
            ),
        }
        if event.name == "manual_motion_started":
            direction = fields.get("direction", "unknown")
            self.state.set_motion(MotionState.RUNNING)
            self.state.set_hardware_status(
                HardwareStatus(active_motion_mode=f"Manual motion {direction}")
            )
            message = f"Manual pivot motion started {direction}."
            severity = EventSeverity.WARNING
        elif event.name == "angle_sensor_unavailable":
            if self.experiment_run.tolerates_transient_angle_dropout:
                severity = EventSeverity.WARNING
                message = (
                    "AS5600 communication degraded: temporarily unavailable during the active experiment; "
                    "missing angle samples remain unavailable while firmware applies its fault threshold."
                )
            else:
                severity = EventSeverity.ERROR
                message = "AS5600 angle sensor communication is unavailable; BIT will fail until a fresh check passes."
        elif event.name == "angle_sensor_restored":
            severity = EventSeverity.INFO
            if self.experiment_run.is_open_loop_vertical_sine_running:
                message = "AS5600 communication restored during open-loop Vertical Sine."
            elif self.experiment_run.tolerates_transient_angle_dropout:
                message = "AS5600 communication restored during the active experiment."
            else:
                message = "AS5600 communication restored; rerun System Check to validate the sensor."
        elif event.name == "manual_motion_stopped":
            direction = fields.get("direction", "unknown")
            reason = fields.get("reason", "unknown")
            if reason in ("upper_limit", "lower_limit", "deadman_timeout", "interlock"):
                severity = EventSeverity.WARNING
            else:
                severity = EventSeverity.INFO
            self.state.set_motion(MotionState.READY if self.state.snapshot.hardware_status.homed else MotionState.IDLE)
            position_steps = self._optional_integer(fields.get("position_steps"))
            current_hardware = self.state.snapshot.hardware_status
            position_mm = (
                position_steps / current_hardware.steps_per_mm
                if position_steps is not None
                and current_hardware.steps_per_mm is not None
                and current_hardware.steps_per_mm > 0
                else None
            )
            self.state.set_hardware_status(
                HardwareStatus(
                    active_motion_mode="Idle",
                    position_steps=position_steps,
                    position_mm=position_mm,
                )
            )
            message = f"Manual pivot motion {direction} stopped: {reason.replace('_', ' ')}."
        elif event.name in messages:
            severity = EventSeverity.INFO
            message = messages[event.name]
            if event.name == "limit_state_changed":
                upper = fields.get("upper")
                lower = fields.get("lower")
                if upper in ("0", "1") and lower in ("0", "1"):
                    self.state.set_hardware_status(
                        HardwareStatus(upper_limit=upper == "1", lower_limit=lower == "1")
                    )
            if event.name.startswith("homing_"):
                self.state.set_home_status(HomeStatus.HOMING)
            if event.name == "limit_state_changed":
                upper = fields.get("upper")
                lower = fields.get("lower")
                if upper in ("0", "1") and lower in ("0", "1"):
                    self.state.set_hardware_status(
                        HardwareStatus(upper_limit=upper == "1", lower_limit=lower == "1")
                    )
        elif event.name == "fault_appeared":
            severity = EventSeverity.ERROR
            fault_code = fields.get("code", "unknown")
            message = f"Firmware fault appeared: {fault_code}."
            self.state.set_hardware_status(
                HardwareStatus(fault=f"Firmware fault {fault_code}", fault_known=True)
            )
            self.state.set_motion(MotionState.FAULT)
        elif event.name == "fault_cleared":
            severity = EventSeverity.INFO
            message = "Firmware reports the blocking fault cleared."
            self.state.set_hardware_status(HardwareStatus(fault=None, fault_known=True))
            self.state.set_motion(
                MotionState.READY if self.state.snapshot.hardware_status.homed else MotionState.IDLE
            )
        elif event.name == "maintenance_command_rejected":
            severity = EventSeverity.WARNING
            message = f"Maintenance command rejected: {fields.get('reason', 'firmware interlock')}."
        else:
            severity = EventSeverity.INFO
            message = event.raw_line
        self.state.report_event(severity, message)

    def _on_telemetry_sequence_gap(self, gap: TelemetrySequenceGap) -> None:
        if gap.out_of_order:
            message = (
                f"Out-of-order/duplicate telemetry sample {gap.received_sample_index}; "
                f"expected {gap.expected_sample_index}."
            )
        else:
            message = (
                f"Telemetry sequence gap: expected sample {gap.expected_sample_index}, "
                f"received {gap.received_sample_index}; missing {gap.missing_count} sample(s)."
            )
        self.state.report_event(EventSeverity.WARNING, message)

    def _on_malformed_line(self, message: str) -> None:
        self.state.report_event(
            EventSeverity.WARNING,
            f"Malformed serial input: {message[:180]}",
        )

    def _on_telemetry_stale(self, stale: bool) -> None:
        was_stale = self.state.snapshot.telemetry_stale
        self.state.set_telemetry_stale(stale)
        if stale:
            self.state.report_event(
                EventSeverity.WARNING,
                "No valid telemetry received for 1.5 seconds.",
            )
        elif was_stale and self.state.snapshot.connection == ConnectionState.CONNECTED:
            self.state.report_event(EventSeverity.INFO, "Valid telemetry stream restored.")

    @staticmethod
    def _optional_integer(value: str | None) -> int | None:
        if value is None:
            return None
        try:
            return int(value)
        except ValueError:
            return None

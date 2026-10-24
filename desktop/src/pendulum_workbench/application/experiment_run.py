import csv
import math
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QTimer, Signal

from pendulum_workbench.application.experiment_recording import ExperimentRecordingService
from pendulum_workbench.application.firmware_commands import (
    FIRMWARE_CLOSED_LOOP_TRAVEL_MARGIN_MM,
    FIRMWARE_MAX_CLOSED_LOOP_AMPLITUDE_MM,
    FirmwareCommand,
    FirmwareCommandFailure,
    FirmwareCommandService,
)
from pendulum_workbench.application.maintenance_service import MaintenanceService
from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.angle_prediction import (
    AngleDropoutPredictor,
    AnglePrediction,
    AngleSource,
)
from pendulum_workbench.domain.experiment_run import (
    AutoResonanceDefinition,
    FreeSwingDefinition,
    ExperimentMode,
    ExperimentDefinition,
    ExperimentRunSnapshot,
    ExperimentRunState,
    VerticalSineDefinition,
    PhaseTrackedSineDefinition,
)
from pendulum_workbench.domain.maintenance import (
    CheckName,
    CheckState,
    PendulumLatchState,
    ReadinessState,
)
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    MotionState,
    RecordingState,
)
from pendulum_workbench.infrastructure.serial_client import TELEMETRY_STALE_AFTER_S


FIRMWARE_MAX_SINE_FREQUENCY_HZ = 10.0
PENDULUM_ANGLE_LIMIT_COMPLETION_REASON = "fault_appeared:pendulum_angle_limit"


class ExperimentRunService(QObject):
    snapshot_changed = Signal(object)

    _TICK_INTERVAL_MS = 100

    def __init__(
        self,
        state: ApplicationStateModel,
        recorder: ExperimentRecordingService,
        commands: FirmwareCommandService,
        maintenance: MaintenanceService,
    ) -> None:
        super().__init__()
        self.state = state
        self.recorder = recorder
        self.commands = commands
        self.maintenance = maintenance
        self._snapshot = ExperimentRunSnapshot()
        self._phase = ""
        self._experiment_id: str | None = None
        self._definition: ExperimentDefinition | None = None
        self._stop_requested = False
        self._pending_final_state = ExperimentRunState.INTERRUPTED
        self._pending_completion_reason = ""
        self._sine_started_monotonic: float | None = None
        self._stop_requested_monotonic: float | None = None
        self._samples_at_start = 0
        self._angle_predictor: AngleDropoutPredictor | None = None
        self._angle_prediction_rows: list[dict[str, object]] = []
        self._timer = QTimer(self)
        self._timer.setInterval(self._TICK_INTERVAL_MS)
        self._timer.timeout.connect(self._on_timer)

        self.recorder.recording_started.connect(self._on_recording_started)
        self.recorder.recording_finished.connect(self._on_recording_finished)
        self.recorder.recording_failed.connect(self._on_recording_failed)
        self.recorder.metadata_update_finished.connect(self._on_metadata_update_finished)
        self.commands.command_completed.connect(self._on_command_completed)
        self.commands.command_failed.connect(self._on_command_failed)
        self.state.snapshot_changed.connect(self._on_application_snapshot)
        self.state.telemetry_received.connect(self._on_telemetry_sample)
        self.maintenance.serial_client.device_event_received.connect(self._on_device_event)
        self.maintenance.serial_client.device_message_received.connect(self._on_device_message)

    @property
    def snapshot(self) -> ExperimentRunSnapshot:
        return self._snapshot

    @property
    def is_active(self) -> bool:
        return self._snapshot.state in (
            ExperimentRunState.PREPARING,
            ExperimentRunState.RUNNING,
            ExperimentRunState.STOPPING,
        )

    @property
    def is_open_loop_vertical_sine_running(self) -> bool:
        definition = self._snapshot.definition
        return (
            self._phase == "running"
            and self._snapshot.state == ExperimentRunState.RUNNING
            and definition is not None
            and definition.mode == ExperimentMode.VERTICAL_SINUSOIDAL_EXCITATION
        )

    @property
    def tolerates_transient_angle_dropout(self) -> bool:
        definition = self._snapshot.definition
        if definition is None:
            return False
        if definition.mode == ExperimentMode.VERTICAL_SINUSOIDAL_EXCITATION:
            return self.is_open_loop_vertical_sine_running
        if definition.mode == ExperimentMode.AUTO_RESONANCE_PARAMETRIC_CLOSED_LOOP:
            return (
                self.is_active
                and self._snapshot.measured_initial_angle_deg is not None
                and self._phase in ("metadata_before_open", "latch_open", "parametric_start", "running", "sine_stop")
            )
        if definition.mode == ExperimentMode.PHASE_TRACKED_SINE_AUTO_RESONANCE:
            return (
                self.is_active
                and self._snapshot.measured_initial_angle_deg is not None
                and self._phase in (
                    "metadata_before_open",
                    "latch_open",
                    "phase_sine_start",
                    "running",
                    "sine_stop",
                )
            )
        return False

    @property
    def setup_locked(self) -> bool:
        return self.is_active

    def start(self, definition: ExperimentDefinition) -> str:
        if self.is_active:
            raise RuntimeError("An experiment run is already active")
        self.validate_start(definition)

        self._definition = definition
        self._stop_requested = False
        self._pending_final_state = ExperimentRunState.INTERRUPTED
        self._pending_completion_reason = ""
        self._sine_started_monotonic = None
        self._stop_requested_monotonic = None
        self._angle_predictor = AngleDropoutPredictor(
            max_prediction_age_s=definition.duration_s
        )
        self._angle_prediction_rows.clear()
        self._phase = "recording_start"
        self._set_snapshot(
            state=ExperimentRunState.PREPARING,
            definition=definition,
            experiment_id=None,
            generated_name=definition.partial_name(),
            directory=None,
            measured_initial_angle_deg=None,
            elapsed_s=0.0,
            actual_elapsed_duration_s=None,
            recording_state=self.state.snapshot.recording.value,
            completion_reason="Preparing experiment run.",
            error_message="",
            started_at=None,
            sample_count=0,
            angle_source=AngleSource.UNAVAILABLE,
            predicted_angle_deg=None,
            prediction_age_ms=None,
            omega_est_deg_s=None,
            prediction_recovery_error_deg=None,
            prediction_outage_duration_ms=None,
        )
        try:
            initial_metadata = definition.manifest_values(measured_initial_angle_deg=None)
            experiment_id = self.recorder.start_recording(
                definition.partial_name(),
                definition.notes,
                experiment_metadata=initial_metadata,
            )
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            self._phase = ""
            self._set_snapshot(
                state=ExperimentRunState.FAULT,
                error_message=str(error),
                completion_reason="Run preparation failed before recording started.",
            )
            raise
        self._experiment_id = experiment_id
        self._set_snapshot(experiment_id=experiment_id)
        return experiment_id

    def validate_start(self, definition: ExperimentDefinition) -> None:
        definition.validate()
        self._validate_start_conditions(definition)

    def stop(self, reason: str = "operator_stop") -> None:
        if not self.is_active or self._snapshot.state == ExperimentRunState.STOPPING:
            return
        self._stop_requested = True
        self._pending_final_state = (
            ExperimentRunState.OPERATOR_STOPPED
            if reason == "operator_stop"
            else ExperimentRunState.INTERRUPTED
        )
        self._pending_completion_reason = reason
        self._stop_requested_monotonic = time.monotonic()
        self._set_snapshot(
            state=ExperimentRunState.STOPPING,
            completion_reason="Operator requested stop." if reason == "operator_stop" else reason,
        )

        if self._phase == "recording_start":
            self._stop_pending_recording()
        elif self._phase == "running":
            if isinstance(self._definition, FreeSwingDefinition):
                self._begin_recording_finalization(
                    self._pending_final_state, self._pending_completion_reason
                )
            else:
                self._issue_sine_stop()

    def _validate_start_conditions(self, definition: ExperimentDefinition) -> None:
        snapshot = self.state.snapshot
        maintenance = self.maintenance.snapshot
        if snapshot.data_source != DataSourceMode.REAL:
            raise RuntimeError("Experiments require a connected real STM32")
        if snapshot.connection != ConnectionState.CONNECTED:
            raise RuntimeError("Connect to the STM32 before running an experiment")
        if any(
            check.name == CheckName.ANGLE_SENSOR and check.state == CheckState.FAIL
            for check in maintenance.checks
        ):
            raise RuntimeError("AS5600 angle is unavailable; restore it and rerun System Check")
        if maintenance.readiness != ReadinessState.SYSTEM_READY:
            raise RuntimeError("Run System Check and Home before running an experiment")
        if snapshot.home_status.value != "HOMED" or snapshot.hardware_status.homed is not True:
            raise RuntimeError("Home the pivot before running an experiment")
        if snapshot.hardware_status.fault_known and snapshot.hardware_status.fault:
            raise RuntimeError("A blocking firmware fault prevents experiment motion")
        if snapshot.motion in (MotionState.HOMING, MotionState.RUNNING, MotionState.STOPPING):
            raise RuntimeError("Stop incompatible pivot motion before running an experiment")
        if snapshot.hardware_status.active_motion_mode.strip().lower() != "idle":
            raise RuntimeError("Firmware motion state is not confirmed idle")
        if snapshot.recording in (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        ) or self.recorder.is_recording:
            raise RuntimeError("Another experiment recording is already active")
        if self.maintenance.is_busy or self.commands.is_busy:
            raise RuntimeError("Wait for the active maintenance command to finish")
        if maintenance.latch_state != PendulumLatchState.HOLD:
            raise RuntimeError("Command Pendulum Latch HOLD and wait for confirmation before RUN")
        span_steps = snapshot.hardware_status.travel_span_steps
        steps_per_mm = snapshot.hardware_status.steps_per_mm
        if not isinstance(definition, FreeSwingDefinition):
            if span_steps is None or steps_per_mm is None or steps_per_mm <= 0.0:
                raise RuntimeError("Homed travel is not available to validate pivot amplitude")
        if isinstance(definition, FreeSwingDefinition):
            pass  # Stationary pivot: no commanded excursion.
        elif isinstance(definition, VerticalSineDefinition):
            if definition.frequency_hz > FIRMWARE_MAX_SINE_FREQUENCY_HZ:
                raise ValueError(
                    f"Frequency exceeds the current firmware command limit of "
                    f"{FIRMWARE_MAX_SINE_FREQUENCY_HZ:g} Hz"
                )
            if definition.amplitude_mm * 2.0 * steps_per_mm >= span_steps:
                raise ValueError("Pivot amplitude must remain strictly within the homed travel")
        elif isinstance(definition, AutoResonanceDefinition):
            if definition.amplitude_mm * 2.0 * steps_per_mm >= span_steps:
                raise ValueError(
                    "Pivot amplitude must remain strictly within the proven parametric half-travel limit"
                )
        elif isinstance(definition, PhaseTrackedSineDefinition):
            if definition.amplitude_mm > FIRMWARE_MAX_CLOSED_LOOP_AMPLITUDE_MM:
                raise ValueError(
                    "Pivot amplitude exceeds the existing firmware closed-loop limit of "
                    f"{FIRMWARE_MAX_CLOSED_LOOP_AMPLITUDE_MM:g} mm"
                )
            available_half_travel_mm = (
                span_steps / (2.0 * steps_per_mm)
                - FIRMWARE_CLOSED_LOOP_TRAVEL_MARGIN_MM
            )
            if definition.amplitude_mm > available_half_travel_mm:
                raise ValueError(
                    "Pivot amplitude exceeds homed travel after the existing closed-loop margin"
                )
        else:
            raise TypeError(f"Unsupported experiment definition: {type(definition).__name__}")
        self._current_valid_angle()

    def _current_valid_angle(self) -> float:
        snapshot = self.state.snapshot
        sample = snapshot.latest_sample
        received_at = snapshot.last_valid_telemetry_at
        if (
            snapshot.data_source != DataSourceMode.REAL
            or snapshot.connection != ConnectionState.CONNECTED
            or snapshot.telemetry_stale
            or sample is None
            or sample.source != "real"
            or sample.angle_deg is None
            or not math.isfinite(sample.angle_deg)
            or received_at is None
        ):
            raise RuntimeError("A fresh valid AS5600 angle is required to start this experiment")
        age_s = (datetime.now(timezone.utc) - received_at.astimezone(timezone.utc)).total_seconds()
        if age_s < 0.0 or age_s > TELEMETRY_STALE_AFTER_S:
            raise RuntimeError("The latest AS5600 angle is stale; wait for fresh telemetry")
        return sample.angle_deg

    def _on_recording_started(self, experiment_id: str, directory: str) -> None:
        if experiment_id != self._experiment_id:
            return
        self._samples_at_start = self.state.snapshot.samples_received
        self._set_snapshot(directory=directory, recording_state=RecordingState.RECORDING.value)
        if self._stop_requested:
            self._stop_pending_recording()
            return

        try:
            measured_angle = self._current_valid_angle()
            definition = self._require_definition()
            if self._angle_predictor is not None:
                measured_sample = self.state.snapshot.latest_sample
                if measured_sample is None:
                    raise RuntimeError("A measured angle sample is required to seed the predictor")
                estimate = self._angle_predictor.observe(measured_sample.time_s, measured_angle)
                self._set_angle_prediction_snapshot(estimate)
            generated_name = definition.final_name(measured_angle)
            self._set_snapshot(
                measured_initial_angle_deg=measured_angle,
                generated_name=generated_name,
            )
            self._phase = "metadata_before_open"
            self.recorder.update_run_metadata(
                experiment_id,
                name=generated_name,
                experiment_metadata=definition.manifest_values(
                    measured_initial_angle_deg=measured_angle,
                    completion_reason="preparing",
                ),
            )
        except (RuntimeError, ValueError) as error:
            self._begin_recording_finalization(
                ExperimentRunState.FAULT,
                f"initial_angle_unavailable: {error}",
            )

    def _on_metadata_update_finished(self, experiment_id: str, error: str) -> None:
        if experiment_id != self._experiment_id:
            return
        if self._phase == "metadata_before_open":
            if error:
                self._begin_recording_finalization(
                    ExperimentRunState.FAULT,
                    f"metadata_write_failed: {error}",
                )
            elif self._stop_requested:
                self._begin_recording_finalization(
                    self._pending_final_state,
                    self._pending_completion_reason,
                )
            else:
                self._issue_latch_open()
        elif self._phase == "metadata_final":
            if error:
                self._pending_final_state = ExperimentRunState.FAULT
                self._pending_completion_reason = f"metadata_write_failed: {error}"
            self._stop_recording_after_metadata()

    def _issue_latch_open(self) -> None:
        self._phase = "latch_open"
        try:
            self.maintenance.command_latch_open_for_experiment()
        except (RuntimeError, ValueError) as error:
            self._begin_recording_finalization(
                ExperimentRunState.FAULT,
                f"latch_open_failed: {error}",
            )

    def _issue_sine_start(self) -> None:
        definition = self._require_definition()
        try:
            if isinstance(definition, AutoResonanceDefinition):
                self._phase = "parametric_start"
                self.commands.start_parametric(
                    definition.amplitude_mm,
                    definition.phase_deg,
                    definition.target_angle_deg,
                )
            elif isinstance(definition, PhaseTrackedSineDefinition):
                self._phase = "phase_sine_start"
                self.commands.start_phase_tracked_sine(
                    definition.amplitude_mm,
                    definition.phase_deg,
                )
            else:
                self._phase = "sine_start"
                self.commands.start_vertical_sine(
                    definition.amplitude_mm,
                    definition.frequency_hz,
                )
        except (RuntimeError, ValueError, OSError) as error:
            self._issue_sine_stop_after_uncertain_start(f"sine_start_failed: {error}")

    def _on_command_completed(self, result: tuple[FirmwareCommand, object]) -> None:
        command, response = result
        if self._phase == "latch_open" and command == FirmwareCommand.LATCH_OPEN:
            if response.fields.get("state") != "open" or response.fields.get("commanded") != "1":
                self._begin_recording_finalization(
                    ExperimentRunState.FAULT,
                    "latch_open_unconfirmed",
                )
            elif self._stop_requested:
                self._begin_recording_finalization(
                    self._pending_final_state,
                    self._pending_completion_reason,
                )
            else:
                if isinstance(self._definition, FreeSwingDefinition):
                    self._sine_started_monotonic = time.monotonic()
                    self._phase = "running"
                    self._timer.start()
                    self._set_snapshot(
                        state=ExperimentRunState.RUNNING,
                        started_at=datetime.now().astimezone().isoformat(),
                        completion_reason="Free swing recording; motor remains idle.",
                    )
                else:
                    self._issue_sine_start()
            return

        start_command = (
            self._phase == "sine_start" and command == FirmwareCommand.SINE_START
        ) or (
            self._phase == "parametric_start"
            and command == FirmwareCommand.PARAMETRIC_START
        ) or (
            self._phase == "phase_sine_start"
            and command == FirmwareCommand.PHASE_TRACKED_SINE_START
        )
        if start_command:
            expected_mode = (
                "parametric"
                if command == FirmwareCommand.PARAMETRIC_START
                else "phase_tracked_sine"
                if command == FirmwareCommand.PHASE_TRACKED_SINE_START
                else None
            )
            if (
                response.fields.get("active") != "1"
                or (
                    expected_mode is not None
                    and response.fields.get("active_mode") != expected_mode
                )
            ):
                self._issue_sine_stop_after_uncertain_start("sine_start_unconfirmed")
            elif self._stop_requested:
                self._sine_started_monotonic = time.monotonic()
                self._issue_sine_stop()
            else:
                self._sine_started_monotonic = time.monotonic()
                self._phase = "running"
                self._timer.start()
                self._set_snapshot(
                    state=ExperimentRunState.RUNNING,
                    started_at=datetime.now().astimezone().isoformat(),
                    completion_reason="Running.",
                )
                self.state.set_motion(MotionState.RUNNING)
            return

        stop_command = self._phase == "sine_stop" and command in (
            FirmwareCommand.SINE_STOP,
            FirmwareCommand.PARAMETRIC_STOP,
            FirmwareCommand.AUTO_RESONANCE_STOP,
            FirmwareCommand.PHASE_TRACKED_SINE_STOP,
        )
        if stop_command:
            expects_idle_mode = command in (
                FirmwareCommand.PARAMETRIC_STOP,
                FirmwareCommand.AUTO_RESONANCE_STOP,
                FirmwareCommand.PHASE_TRACKED_SINE_STOP,
            )
            if response.fields.get("active") != "0" or (
                expects_idle_mode and response.fields.get("active_mode") != "idle"
            ):
                if self._pending_completion_reason != PENDULUM_ANGLE_LIMIT_COMPLETION_REASON:
                    self._pending_final_state = ExperimentRunState.FAULT
                    self._pending_completion_reason = "sine_stop_unconfirmed"
            self._set_motion_after_sine_stop()
            self._observe_after_successful_stop()

    def _on_command_failed(self, failure: FirmwareCommandFailure) -> None:
        if self._experiment_id is None:
            return
        if self.state.snapshot.connection != ConnectionState.CONNECTED:
            if self._pending_completion_reason == PENDULUM_ANGLE_LIMIT_COMPLETION_REASON:
                self._begin_recording_finalization(
                    ExperimentRunState.FAULT,
                    PENDULUM_ANGLE_LIMIT_COMPLETION_REASON,
                )
                return
            self._begin_recording_finalization(
                ExperimentRunState.INTERRUPTED,
                "connection_lost",
            )
        elif self._phase == "latch_open" and failure.command == FirmwareCommand.LATCH_OPEN:
            self._begin_recording_finalization(
                ExperimentRunState.FAULT,
                f"latch_open_failed: {failure.message}",
            )
        elif self._phase in ("sine_start", "parametric_start", "phase_sine_start") and failure.command in (
            FirmwareCommand.SINE_START,
            FirmwareCommand.PARAMETRIC_START,
            FirmwareCommand.AUTO_RESONANCE_START,
            FirmwareCommand.PHASE_TRACKED_SINE_START,
        ):
            self._issue_sine_stop_after_uncertain_start(f"sine_start_failed: {failure.message}")
        elif self._phase == "sine_stop" and failure.command in (
            FirmwareCommand.SINE_STOP,
            FirmwareCommand.PARAMETRIC_STOP,
            FirmwareCommand.AUTO_RESONANCE_STOP,
            FirmwareCommand.PHASE_TRACKED_SINE_STOP,
        ):
            self._pending_final_state = ExperimentRunState.FAULT
            if self._pending_completion_reason != PENDULUM_ANGLE_LIMIT_COMPLETION_REASON:
                self._pending_completion_reason = f"sine_stop_failed: {failure.message}"
            self._begin_recording_finalization(
                ExperimentRunState.FAULT,
                self._pending_completion_reason,
            )

    def _issue_sine_stop_after_uncertain_start(self, reason: str) -> None:
        if self.state.snapshot.connection != ConnectionState.CONNECTED:
            self._begin_recording_finalization(ExperimentRunState.INTERRUPTED, reason)
            return
        if self._pending_final_state != ExperimentRunState.OPERATOR_STOPPED:
            self._pending_final_state = ExperimentRunState.FAULT
            self._pending_completion_reason = reason
        self._set_snapshot(state=ExperimentRunState.STOPPING, error_message=reason)
        self._issue_sine_stop()

    def _issue_sine_stop(self) -> None:
        if self.state.snapshot.connection != ConnectionState.CONNECTED:
            if self._pending_completion_reason == PENDULUM_ANGLE_LIMIT_COMPLETION_REASON:
                self._begin_recording_finalization(
                    ExperimentRunState.FAULT,
                    PENDULUM_ANGLE_LIMIT_COMPLETION_REASON,
                )
                return
            self._begin_recording_finalization(
                ExperimentRunState.INTERRUPTED,
                self._pending_completion_reason or "connection_lost",
            )
            return
        self._phase = "sine_stop"
        try:
            definition = self._require_definition()
            if isinstance(definition, AutoResonanceDefinition):
                self.commands.stop_parametric()
            elif isinstance(definition, PhaseTrackedSineDefinition):
                self.commands.stop_phase_tracked_sine()
            else:
                self.commands.stop_vertical_sine()
        except (RuntimeError, ValueError, OSError) as error:
            self._pending_final_state = ExperimentRunState.FAULT
            if self._pending_completion_reason != PENDULUM_ANGLE_LIMIT_COMPLETION_REASON:
                self._pending_completion_reason = f"sine_stop_failed: {error}"
            self._begin_recording_finalization(
                ExperimentRunState.FAULT,
                self._pending_completion_reason,
            )

    def _set_motion_after_sine_stop(self) -> None:
        hardware = self.state.snapshot.hardware_status
        if hardware.fault_known and hardware.fault:
            self.state.set_motion(MotionState.FAULT)
        else:
            self.state.set_motion(MotionState.READY if hardware.homed else MotionState.IDLE)

    def _begin_recording_finalization(
        self,
        final_state: ExperimentRunState,
        reason: str,
    ) -> None:
        if self._phase in ("metadata_final", "recording_finalization", "terminal"):
            return
        self._timer.stop()
        self._pending_final_state = final_state
        self._pending_completion_reason = reason
        if self._sine_started_monotonic is not None:
            actual_duration = self._stop_requested_monotonic or time.monotonic()
            actual_duration = max(0.0, actual_duration - self._sine_started_monotonic)
        else:
            actual_duration = 0.0
        self._set_snapshot(
            state=ExperimentRunState.STOPPING,
            elapsed_s=actual_duration,
            actual_elapsed_duration_s=actual_duration,
            completion_reason=reason,
        )

        if not self.recorder.is_recording or self._experiment_id is None:
            self._publish_terminal(final_state, reason, actual_duration)
            return

        if self.recorder.active_session is None:
            self._phase = "recording_finalization"
            self.recorder.stop_recording(interrupted=True, reason=reason)
            return

        definition = self._require_definition()
        measured_angle = self._snapshot.measured_initial_angle_deg
        final_name = (
            definition.final_name(measured_angle)
            if measured_angle is not None
            else definition.partial_name()
        )
        self._phase = "metadata_final"
        try:
            self.recorder.update_run_metadata(
                self._experiment_id,
                name=final_name,
                experiment_metadata=definition.manifest_values(
                    measured_initial_angle_deg=measured_angle,
                    actual_elapsed_duration_s=actual_duration,
                    completion_reason=reason,
                ),
            )
        except (RuntimeError, ValueError) as error:
            self._pending_final_state = ExperimentRunState.FAULT
            self._pending_completion_reason = f"metadata_write_failed: {error}"
            self._stop_recording_after_metadata()

    def _finish_post_stop_observation(self) -> None:
        if self._phase != "post_stop_observation":
            return
        self._begin_recording_finalization(
            self._pending_final_state,
            self._pending_completion_reason,
        )

    def _observe_after_successful_stop(self) -> None:
        # Keep capturing telemetry after the motor stops. The pendulum can
        # continue moving under its own momentum. This does not delay motor stop.
        if self._pending_final_state != ExperimentRunState.COMPLETE:
            self._begin_recording_finalization(
                self._pending_final_state, self._pending_completion_reason
            )
            return
        self._phase = "post_stop_observation"
        self.state.report_event(
            EventSeverity.INFO,
            "Excitation stopped; recording 5 additional seconds of free swing.",
        )
        QTimer.singleShot(5000, self._finish_post_stop_observation)

    def _stop_recording_after_metadata(self) -> None:
        if not self.recorder.is_recording:
            self._publish_terminal(
                self._pending_final_state,
                self._pending_completion_reason,
                self._snapshot.actual_elapsed_duration_s or 0.0,
            )
            return
        self._phase = "recording_finalization"
        interrupted = self._pending_final_state != ExperimentRunState.COMPLETE
        self.recorder.stop_recording(
            interrupted=interrupted,
            reason=self._pending_completion_reason if interrupted else None,
        )

    def _stop_pending_recording(self) -> None:
        if self.recorder.is_recording:
            self._phase = "recording_finalization"
            self.recorder.stop_recording(
                interrupted=True,
                reason=self._pending_completion_reason or "operator_stop_during_preparation",
            )
        else:
            self._publish_terminal(
                self._pending_final_state,
                self._pending_completion_reason,
                0.0,
            )

    def _on_recording_finished(self, experiment_id: str, status: str) -> None:
        if experiment_id != self._experiment_id:
            return
        if status == "INTERRUPTED" and self._pending_final_state == ExperimentRunState.COMPLETE:
            self._pending_final_state = ExperimentRunState.INTERRUPTED
            if not self._pending_completion_reason:
                self._pending_completion_reason = "recording_interrupted"
        self._phase = "terminal"
        self._timer.stop()
        try:
            self._write_angle_prediction_data()
        except OSError as error:
            self.state.report_event(
                EventSeverity.WARNING,
                f"Could not save derived angle prediction data: {error}",
            )
        self._publish_terminal(
            self._pending_final_state,
            self._pending_completion_reason,
            self._snapshot.actual_elapsed_duration_s or 0.0,
            recording_state=self.state.snapshot.recording.value,
        )

    def _on_telemetry_sample(self, sample) -> None:
        # A target is reached only by a valid, measured angle while actively running.
        # Never reinterpret a firmware safety fault as a successful target event.
        if (
            self._phase == "running"
            and self._snapshot.state == ExperimentRunState.RUNNING
            and self._definition is not None
            and not isinstance(self._definition, FreeSwingDefinition)
            and sample.source == "real"
            and sample.angle_deg is not None
            and math.isfinite(sample.angle_deg)
            and abs(sample.angle_deg) >= self._definition.target_angle_deg
        ):
            hardware = self.state.snapshot.hardware_status
            if not (hardware.fault_known and hardware.fault):
                self._pending_final_state = ExperimentRunState.COMPLETE
                self._pending_completion_reason = "TARGET_REACHED"
                self._stop_requested_monotonic = time.monotonic()
                self._set_snapshot(
                    state=ExperimentRunState.STOPPING,
                    completion_reason="TARGET_REACHED",
                )
                self._issue_sine_stop()
                return
        if (
            self._phase not in ("metadata_before_open", "latch_open", "sine_start", "running")
            or self._angle_predictor is None
            or sample.source != "real"
            or self._definition is None
            or self._definition.mode != ExperimentMode.VERTICAL_SINUSOIDAL_EXCITATION
        ):
            return

        is_running = self.is_open_loop_vertical_sine_running
        if not is_running and sample.angle_deg is None:
            return

        estimate = self._angle_predictor.observe(
            sample.time_s,
            sample.angle_deg,
        )
        if is_running and (
            estimate.angle_source != AngleSource.MEASURED
            or estimate.prediction_recovery_error_deg is not None
        ):
            self._angle_prediction_rows.append(
                {
                    "sample_index": sample.device_sample_index or sample.sample_index,
                    "time_s": sample.time_s,
                    "angle_source": estimate.angle_source.value,
                    "predicted_angle_deg": estimate.predicted_angle_deg,
                    "prediction_age_ms": estimate.prediction_age_ms,
                    "omega_est_deg_s": estimate.omega_est_deg_s,
                    "prediction_recovery_error_deg": estimate.prediction_recovery_error_deg,
                    "outage_duration_ms": estimate.outage_duration_ms,
                }
            )
            if estimate.prediction_recovery_error_deg is not None:
                self.state.report_event(
                    EventSeverity.INFO,
                    "Angle prediction recovered: "
                    f"prediction_recovery_error_deg={estimate.prediction_recovery_error_deg:.6f}, "
                    f"outage_duration_ms={estimate.outage_duration_ms:.3f}.",
                )

        self._set_angle_prediction_snapshot(estimate)

    def _set_angle_prediction_snapshot(self, estimate: AnglePrediction) -> None:
        self._set_snapshot(
            angle_source=estimate.angle_source,
            predicted_angle_deg=estimate.predicted_angle_deg,
            prediction_age_ms=estimate.prediction_age_ms,
            omega_est_deg_s=estimate.omega_est_deg_s,
            prediction_recovery_error_deg=estimate.prediction_recovery_error_deg,
            prediction_outage_duration_ms=estimate.outage_duration_ms,
        )

    def _write_angle_prediction_data(self) -> None:
        directory = self._snapshot.directory
        if not directory or not self._angle_prediction_rows:
            return
        destination = Path(directory) / "derived" / "angle_prediction.csv"
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="") as output_file:
            writer = csv.DictWriter(
                output_file,
                fieldnames=(
                    "sample_index",
                    "time_s",
                    "angle_source",
                    "predicted_angle_deg",
                    "prediction_age_ms",
                    "omega_est_deg_s",
                    "prediction_recovery_error_deg",
                    "outage_duration_ms",
                ),
                lineterminator="\n",
            )
            writer.writeheader()
            writer.writerows(self._angle_prediction_rows)

    def _on_recording_failed(self, experiment_id: str, message: str) -> None:
        if experiment_id != self._experiment_id:
            return
        if self._phase == "running":
            self._pending_final_state = ExperimentRunState.FAULT
            self._pending_completion_reason = f"recording_failed: {message}"
            self._stop_requested_monotonic = time.monotonic()
            self._set_snapshot(state=ExperimentRunState.STOPPING, error_message=message)
            if isinstance(self._definition, FreeSwingDefinition):
                self._begin_recording_finalization(
                    ExperimentRunState.FAULT, self._pending_completion_reason
                )
            else:
                self._issue_sine_stop()
            return
        if self._phase in ("sine_start", "parametric_start", "phase_sine_start"):
            self._stop_requested = True
            self._pending_final_state = ExperimentRunState.FAULT
            self._pending_completion_reason = f"recording_failed: {message}"
            self._set_snapshot(state=ExperimentRunState.STOPPING, error_message=message)
            return
        self._timer.stop()
        self._phase = "terminal"
        self._publish_terminal(
            ExperimentRunState.FAULT,
            f"recording_failed: {message}",
            self._snapshot.actual_elapsed_duration_s or 0.0,
            error_message=message,
            recording_state=self.state.snapshot.recording.value,
        )

    def _on_application_snapshot(self, snapshot) -> None:
        if not self.is_active:
            return
        self._set_snapshot(
            recording_state=snapshot.recording.value,
            sample_count=self.recorder.active_sample_count,
        )
        if snapshot.connection != ConnectionState.CONNECTED and self._phase != "recording_finalization":
            self._stop_requested = True
            angle_limit_fault = (
                self._pending_completion_reason == PENDULUM_ANGLE_LIMIT_COMPLETION_REASON
            )
            self._pending_final_state = (
                ExperimentRunState.FAULT
                if angle_limit_fault
                else ExperimentRunState.INTERRUPTED
            )
            if not angle_limit_fault:
                self._pending_completion_reason = "connection_lost"
            self._begin_recording_finalization(
                self._pending_final_state,
                self._pending_completion_reason,
            )

    def _on_device_event(self, event) -> None:
        if not self.is_active:
            return
        if event.name == "experiment_target_reached":
            # Firmware can report the target after a telemetry sample has already
            # initiated the desktop stop. Do not issue a duplicate stop request.
            self.state.report_event(
                EventSeverity.INFO,
                "STM32 confirmed target angle reached"
                + (f" at {event.fields['angle_deg']} deg" if event.fields.get("angle_deg") else "")
                + ".",
            )
            if (self._phase == "running"
                    and self._snapshot.state == ExperimentRunState.RUNNING
                    and not isinstance(self._definition, FreeSwingDefinition)):
                self._pending_final_state = ExperimentRunState.COMPLETE
                self._pending_completion_reason = "TARGET_REACHED"
                self._stop_requested_monotonic = time.monotonic()
                self._set_snapshot(state=ExperimentRunState.STOPPING, completion_reason="TARGET_REACHED")
                self._issue_sine_stop()
            return
        if event.name == "sine_motion_stopped":
            self._handle_safety_interruption(
                f"sine_motion_stopped:{event.fields.get('reason', 'unknown')}",
                final_state=ExperimentRunState.INTERRUPTED,
            )
        elif event.name == "angle_sensor_unavailable":
            if not self.tolerates_transient_angle_dropout:
                self._handle_safety_interruption(f"safety_event:{event.name}")
        elif event.name == "fault_appeared":
            fault_code = event.fields.get("code")
            reason = (
                PENDULUM_ANGLE_LIMIT_COMPLETION_REASON
                if fault_code == "pendulum_angle_limit"
                else f"safety_event:fault_appeared:{fault_code}"
                if fault_code
                else "safety_event:fault_appeared"
            )
            self._handle_safety_interruption(reason)

    def _on_device_message(self, message: str) -> None:
        if (
            self.is_active
            and message.strip().lower() == "angle sensor unavailable"
            and not self.tolerates_transient_angle_dropout
        ):
            self._handle_safety_interruption("safety_event:angle_sensor_unavailable")

    def _handle_safety_interruption(
        self,
        reason: str,
        *,
        final_state: ExperimentRunState = ExperimentRunState.FAULT,
    ) -> None:
        self._stop_requested = True
        self._pending_final_state = final_state
        self._pending_completion_reason = reason
        if self._phase == "running":
            self._stop_requested_monotonic = time.monotonic()
            self._set_snapshot(state=ExperimentRunState.STOPPING, completion_reason=reason)
            if isinstance(self._definition, FreeSwingDefinition):
                self._begin_recording_finalization(final_state, reason)
            else:
                self._issue_sine_stop()
        elif self._phase in (
            "metadata_before_open",
            "latch_open",
            "sine_start",
            "parametric_start",
            "phase_sine_start",
        ):
            self._set_snapshot(state=ExperimentRunState.STOPPING, completion_reason=reason)
        elif self._phase == "recording_start":
            self._set_snapshot(state=ExperimentRunState.STOPPING, completion_reason=reason)
            self._stop_pending_recording()
        elif self._phase not in ("sine_stop", "recording_finalization", "terminal"):
            self._begin_recording_finalization(final_state, reason)

    def _on_timer(self) -> None:
        if self._snapshot.state != ExperimentRunState.RUNNING or self._sine_started_monotonic is None:
            return
        elapsed = max(0.0, time.monotonic() - self._sine_started_monotonic)
        self._set_snapshot(
            elapsed_s=elapsed,
            sample_count=max(0, self.state.snapshot.samples_received - self._samples_at_start),
            recording_state=self.state.snapshot.recording.value,
        )
        definition = self._require_definition()
        if elapsed >= definition.duration_s:
            self._pending_final_state = ExperimentRunState.COMPLETE
            self._pending_completion_reason = "duration_elapsed"
            self._stop_requested_monotonic = time.monotonic()
            self._set_snapshot(state=ExperimentRunState.STOPPING)
            if isinstance(definition, FreeSwingDefinition):
                self._begin_recording_finalization(
                    self._pending_final_state, self._pending_completion_reason
                )
            else:
                self._issue_sine_stop()

    def _publish_terminal(
        self,
        state: ExperimentRunState,
        reason: str,
        actual_duration: float,
        *,
        error_message: str = "",
        recording_state: str | None = None,
    ) -> None:
        self._phase = "terminal"
        self._timer.stop()
        self._set_snapshot(
            state=state,
            elapsed_s=actual_duration,
            actual_elapsed_duration_s=actual_duration,
            completion_reason=reason,
            error_message=error_message,
            recording_state=recording_state or self.state.snapshot.recording.value,
        )

    def _require_definition(self) -> ExperimentDefinition:
        if self._definition is None:
            raise RuntimeError("No experiment definition is active")
        return self._definition

    def _set_snapshot(self, **changes: object) -> None:
        updated = replace(self._snapshot, **changes)
        if updated != self._snapshot:
            self._snapshot = updated
            self.snapshot_changed.emit(updated)
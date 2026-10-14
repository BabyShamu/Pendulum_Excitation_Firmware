import csv
import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.application.firmware_commands import FirmwareCommand
from pendulum_workbench.domain.angle_prediction import AngleSource
from pendulum_workbench.domain.experiment_run import (
    AutoResonanceDefinition,
    ExperimentRunState,
    PhaseTrackedSineDefinition,
    VerticalSineDefinition,
)
from pendulum_workbench.domain.maintenance import BitResultState, PendulumLatchState, ReadinessState
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    HardwareStatus,
    HomeStatus,
    MotionState,
    RecordingState,
)
from pendulum_workbench.domain.models import TelemetrySample
from pendulum_workbench.experiments.repository import ExperimentRepository
from pendulum_workbench.infrastructure.serial_client import SerialTelemetryClient
from pendulum_workbench.infrastructure.firmware_protocol import MaintenanceResponse
from pendulum_workbench.ui.main_window import MainWindow


class SineExperimentSerial:
    def __init__(
        self,
        port: str,
        baud_rate: int,
        *,
        angle_available: bool = True,
        initially_homed: bool = False,
        reject_latch_open: bool = False,
        reject_sine_start: bool = False,
        defer_latch_hold: bool = False,
        defer_latch_open: bool = False,
        suppress_parametric_stop_response: bool = False,
        **kwargs: object,
    ) -> None:
        del port, baud_rate, kwargs
        self.angle_available = angle_available
        self.reject_latch_open = reject_latch_open
        self.reject_sine_start = reject_sine_start
        self.defer_latch_hold = defer_latch_hold
        self.defer_latch_open = defer_latch_open
        self.suppress_parametric_stop_response = suppress_parametric_stop_response
        self._chunks: list[bytes] = []
        self._live_enabled = False
        self._next_sample_index = 1
        self._start_monotonic = time.monotonic()
        self._next_sample_monotonic = self._start_monotonic
        self._pending_open: str | None = None
        self._pending_hold: str | None = None
        self._pending_open_rejected = False
        self.writes: list[bytes] = []
        self.protocol_actions: list[str] = []
        self.latch_commands: list[str] = []
        self.recording_state_when_open_commanded: RecordingState | None = None
        self.initial_angle_at_latch_open: float | None = None
        self.manifest_at_latch_open: dict | None = None
        self.latch_state = "unknown"
        self.homed = initially_homed
        self.sine_active = False
        self.autores_active = False
        self.phase_tracked_sine_active = False
        self.autores_amplitude_mm: float | None = None
        self.autores_phase_deg: float | None = None
        self.sine_amplitude_mm: float | None = None
        self.sine_frequency_hz: float | None = None
        self.home_motion_start_count = 0
        self.closed = False

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        text = data.decode("ascii", errors="replace").strip()
        fields = text.split()
        if text == "live 1":
            self._live_enabled = True
            self._chunks.append(b"\r\nlive telemetry enabled\r\n")
        elif len(fields) >= 3 and fields[0] == "maint":
            request_id, command = fields[1], fields[2]
            self.protocol_actions.append(command)
            if command.startswith("latch_"):
                self.latch_commands.append(command)
            if command == "status":
                mode = (
                    "phase_tracked_sine"
                    if self.phase_tracked_sine_active
                    else
                    "parametric_closed_loop"
                    if self.autores_active
                    else "sine"
                    if self.sine_active
                    else "idle"
                )
                self._enqueue(
                    f"maintenance: id={request_id} type=status ok=1 homed={int(self.homed)} "
                    f"upper_limit=0 lower_limit=0 fault=0 active_mode={mode} "
                    "travel_span_steps=1200 position_steps=0 steps_per_mm=40.000\r\n"
                )
            elif command == "switches":
                self._enqueue(f"maintenance: id={request_id} type=switches ok=1 upper=0 lower=0\r\n")
            elif command == "angle":
                if self.angle_available:
                    self._enqueue(
                        f"maintenance: id={request_id} type=angle ok=1 raw=2100 degrees=14.80\r\n"
                    )
                else:
                    self._enqueue(
                        f"maintenance: id={request_id} type=angle ok=0 error=as5600_unavailable\r\n"
                    )
            elif command == "home":
                if self.latch_state != "open":
                    self._enqueue(
                        f"maintenance: id={request_id} type=home ok=0 homed=0 fault=0 error=latch_not_open\r\n"
                    )
                else:
                    self.home_motion_start_count += 1
                    self.homed = True
                    self._enqueue(
                        f"maintenance: id={request_id} type=home ok=1 homed=1 fault=0 "
                        "span_steps=1200 position_steps=0 steps_per_mm=40.000\r\n"
                    )
            elif command == "latch_hold":
                if self.defer_latch_hold:
                    self._pending_hold = request_id
                else:
                    self.latch_state = "hold"
                    self._enqueue(
                        f"maintenance: id={request_id} type=latch_hold ok=1 state=hold commanded=1 pulse_us=700\r\n"
                    )
            elif command == "latch_open":
                self.recording_state_when_open_commanded = self._controller.state.snapshot.recording
                latest_sample = self._controller.state.snapshot.latest_sample
                self.initial_angle_at_latch_open = (
                    latest_sample.angle_deg if latest_sample is not None else None
                )
                active_session = self._controller.experiment_recorder.active_session
                self.manifest_at_latch_open = (
                    dict(active_session.manifest) if active_session is not None else None
                )
                if self.defer_latch_open:
                    self._pending_open = request_id
                    self._pending_open_rejected = self.reject_latch_open
                elif self.reject_latch_open:
                    self._enqueue(
                        f"maintenance: id={request_id} type=latch_open ok=0 state={self.latch_state} "
                        "error=servo_test_rejected\r\n"
                    )
                else:
                    self.latch_state = "open"
                    self._enqueue(
                        f"maintenance: id={request_id} type=latch_open ok=1 state=open commanded=1 pulse_us=1700\r\n"
                    )
            elif command == "latch_status":
                self._enqueue(
                    f"maintenance: id={request_id} type=latch_status ok=1 state={self.latch_state} "
                    f"commanded={int(self.latch_state != 'unknown')}\r\n"
                )
            elif command == "sine_start":
                amplitude = float(fields[3])
                frequency = float(fields[4])
                if self.reject_sine_start:
                    self._enqueue(
                        f"maintenance: id={request_id} type=sine_start ok=0 active=0 error=sine_test_rejected\r\n"
                    )
                else:
                    self.sine_active = True
                    self.sine_amplitude_mm = amplitude
                    self.sine_frequency_hz = frequency
                    self._enqueue(
                        f"maintenance: id={request_id} type=sine_start ok=1 active=1 "
                        f"amplitude_mm={amplitude:.6f} frequency_hz={frequency:.6f}\r\n"
                    )
            elif command == "sine_stop":
                self.sine_active = False
                self._enqueue(
                    f"maintenance: id={request_id} type=sine_stop ok=1 active=0 position_steps=0\r\n"
                )
            elif command in ("autores_start", "parametric_start"):
                amplitude = float(fields[3])
                phase = float(fields[4])
                self.autores_active = True
                self.autores_amplitude_mm = amplitude
                self.autores_phase_deg = phase
                active_mode = "parametric" if command == "parametric_start" else "parametric_closed_loop"
                self._enqueue(
                    f"maintenance: id={request_id} type={command} ok=1 active=1 "
                    f"active_mode={active_mode} amplitude_mm={amplitude:.6f} "
                    f"phase_deg={phase:.6f}\r\n"
                )
            elif command in ("autores_stop", "parametric_stop"):
                self.autores_active = False
                if command == "parametric_stop":
                    self._enqueue(
                        "time_s,theta_deg,controller_mm,motor_target_mm,actual_mm,target_steps,actual_steps,step_error\r\n"
                    )
                    self._enqueue("0.020,1.0,0.5,0.5,0.4,20,16,4\r\n")
                    self._enqueue("telemetry sample_index=90 time_s=2.0 angle_deg=14.8 position_mm=0.4\r\n")
                if not (command == "parametric_stop" and self.suppress_parametric_stop_response):
                    self._enqueue(
                        f"maintenance: id={request_id} type={command} ok=1 active=0 active_mode=idle\r\n"
                    )
                if command == "parametric_stop":
                    self._enqueue("event: type=limit_state_changed upper=0 lower=0\r\n")
            elif command == "phase_sine_start":
                amplitude = float(fields[3])
                phase = float(fields[4])
                self.phase_tracked_sine_active = True
                self.autores_amplitude_mm = amplitude
                self.autores_phase_deg = phase
                self._enqueue(
                    f"maintenance: id={request_id} type=phase_sine_start ok=1 active=1 "
                    f"active_mode=phase_tracked_sine amplitude_mm={amplitude:.6f} "
                    f"phase_deg={phase:.6f}\r\n"
                )
            elif command == "phase_sine_stop":
                self.phase_tracked_sine_active = False
                self._enqueue(
                    f"maintenance: id={request_id} type=phase_sine_stop ok=1 active=0 active_mode=idle\r\n"
                )
        return len(data)

    def release_latch_open(self) -> None:
        request_id = self._pending_open
        self._pending_open = None
        self.defer_latch_open = False
        if request_id is not None:
            if self._pending_open_rejected:
                self._enqueue(
                    f"maintenance: id={request_id} type=latch_open ok=0 state={self.latch_state} "
                    "error=servo_test_rejected\r\n"
                )
            else:
                self.latch_state = "open"
                self._enqueue(
                    f"maintenance: id={request_id} type=latch_open ok=1 state=open commanded=1 pulse_us=1700\r\n"
                )
        self._pending_open_rejected = False

    def release_latch_hold(self) -> None:
        request_id = self._pending_hold
        self._pending_hold = None
        self.defer_latch_hold = False
        if request_id is not None:
            self.latch_state = "hold"
            self._enqueue(
                f"maintenance: id={request_id} type=latch_hold ok=1 state=hold commanded=1 pulse_us=700\r\n"
            )

    def stop_sine_by_limit(self) -> None:
        self.sine_active = False
        self._enqueue("event: type=sine_motion_stopped reason=limit\r\n")

    def report_angle_sensor_unavailable(self) -> None:
        self.angle_available = False
        self._enqueue("event: type=angle_sensor_unavailable failures=3 hal_error=2\r\n")

    def report_angle_sensor_restored(self) -> None:
        self.angle_available = True
        self._enqueue("event: type=angle_sensor_restored\r\n")

    def report_closed_loop_fault(self, code: str = "upper_limit") -> None:
        self.autores_active = False
        self._enqueue(f"event: type=fault_appeared code={code}\r\n")

    def _enqueue(self, text: str) -> None:
        if "type=latch_open" in text:
            self.recording_state_when_open_commanded = self._controller.state.snapshot.recording
        self._chunks.append(text.encode("ascii"))

    def read(self, size: int) -> bytes:
        del size
        if self._chunks:
            return self._chunks.pop(0)
        now = time.monotonic()
        if self._live_enabled and now >= self._next_sample_monotonic:
            self._next_sample_monotonic = now + 0.02
            sample_index = self._next_sample_index
            self._next_sample_index += 1
            elapsed_s = now - self._start_monotonic
            angle = "14.80" if self.angle_available else "NaN"
            return (
                f"telemetry sample_index={sample_index} time_s={elapsed_s:.3f} "
                f"angle_deg={angle} position_mm=0.000\r\n"
            ).encode("ascii")
        time.sleep(0.002)
        return b""

    def close(self) -> None:
        self.closed = True


class ExperimentRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout_s: float = 3.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if predicate():
                return True
            time.sleep(0.003)
        self.qt_app.processEvents()
        return predicate()

    def temporary_directory(self) -> str:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        return temporary_directory.name

    def create_connected_controller(self, temporary_directory: str, **serial_options):
        devices: list[SineExperimentSerial] = []

        def serial_factory(port: str, baud_rate: int, **kwargs: object) -> SineExperimentSerial:
            device = SineExperimentSerial(port, baud_rate, **serial_options, **kwargs)
            devices.append(device)
            return device

        repository = ExperimentRepository(Path(temporary_directory))
        client = SerialTelemetryClient(
            capture_root=Path(temporary_directory) / "captures",
            serial_factory=serial_factory,
            startup_delay_s=0.0,
        )
        controller = WorkbenchController(serial_client=client, experiment_repository=repository)
        controller.select_data_source(DataSourceMode.REAL)
        controller.connect_real("COM_SINE_TEST")
        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.connection == ConnectionState.CONNECTED
                and controller.state.snapshot.hardware_status.homed is not None
                and not controller.firmware_commands.is_busy
            )
        )
        device = devices[0]
        device._controller = controller
        return controller, device, repository

    def prepare_ready_held_system(self, controller: WorkbenchController, device: SineExperimentSerial) -> None:
        controller.maintenance.run_system_check()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.bit_state == BitResultState.PASSED
                and not controller.firmware_commands.is_busy
            )
        )
        controller.maintenance.command_latch(PendulumLatchState.OPEN)
        opened = self.wait_for(
            lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.OPEN
            and not controller.firmware_commands.is_busy
        )
        raw_path = Path(controller.state.snapshot.raw_capture_path)
        raw_tail = raw_path.read_text(encoding="utf-8").splitlines()[-8:]
        self.assertTrue(
            opened,
            f"latch={controller.maintenance.snapshot.latch_state}, "
            f"operation={controller.maintenance.snapshot.operation}, "
            f"command={controller.firmware_commands._active_command}, "
            f"actions={device.protocol_actions!r}, queued={device._chunks!r}, raw_tail={raw_tail!r}",
        )
        controller.maintenance.start_homing()
        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.home_status == HomeStatus.HOMED
                and not controller.firmware_commands.is_busy
            )
        )
        controller.maintenance.command_latch(PendulumLatchState.HOLD)
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.HOLD
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(device.latch_state, "hold")

    def test_preflight_requires_hold_and_valid_as5600_angle(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        device.angle_available = False
        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.latest_sample is not None
                and controller.state.snapshot.latest_sample.angle_deg is None
            )
        )
        with self.assertRaisesRegex(RuntimeError, "AS5600 angle"):
            controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 30.0))
        with self.assertRaisesRegex(RuntimeError, "AS5600 angle"):
            controller.experiment_run.start(AutoResonanceDefinition(5.0, 0.0, 30.0))
        self.assertEqual(controller.experiment_run.snapshot.state, ExperimentRunState.READY)
        self.assertFalse(controller.experiment_recorder.is_recording)
        self.assertEqual(device.latch_state, "hold")

    def test_as5600_loss_at_initial_angle_capture_still_faults_before_latch_open(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        device.protocol_actions.clear()
        current_angle = controller.experiment_run._current_valid_angle

        with patch.object(
            controller.experiment_run,
            "_current_valid_angle",
            side_effect=[current_angle(), RuntimeError("AS5600 became unavailable")],
        ):
            controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 30.0))
            self.assertTrue(
                self.wait_for(
                    lambda: controller.experiment_run.snapshot.state == ExperimentRunState.FAULT
                    and controller.state.snapshot.recording == RecordingState.INTERRUPTED
                )
            )

        self.assertNotIn("latch_open", device.protocol_actions)
        self.assertNotIn("sine_start", device.protocol_actions)

    def test_unknown_and_open_latch_block_run_without_automatic_hold(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(
            directory,
            initially_homed=True,
        )
        self.addCleanup(controller.shutdown)
        controller.maintenance.run_system_check()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.readiness == ReadinessState.SYSTEM_READY
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(controller.maintenance.snapshot.latch_state, PendulumLatchState.UNKNOWN)
        self.assertEqual(device.latch_commands, [])

        definition = VerticalSineDefinition(5.0, 1.8, 30.0)
        with self.assertRaisesRegex(RuntimeError, "Latch HOLD"):
            controller.experiment_run.start(definition)
        self.assertFalse(controller.experiment_recorder.is_recording)

        controller.maintenance.command_latch(PendulumLatchState.OPEN)
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.OPEN
                and not controller.firmware_commands.is_busy
            )
        )
        with self.assertRaisesRegex(RuntimeError, "Latch HOLD"):
            controller.experiment_run.start(definition)
        self.assertEqual(device.latch_commands, ["latch_open"])
        self.assertFalse(controller.experiment_recorder.is_recording)

    def test_start_records_angle_then_opens_latch_then_starts_sine(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        device.defer_latch_open = True
        device.protocol_actions.clear()
        device.recording_state_when_open_commanded = None

        experiment_id = controller.experiment_run.start(
            VerticalSineDefinition(5.0, 1.8, 30.0, label="Trial / A", notes="initial note")
        )

        self.assertTrue(self.wait_for(lambda: device._pending_open is not None))
        self.assertEqual(controller.experiment_run.snapshot.state, ExperimentRunState.PREPARING)
        self.assertFalse(device.sine_active)
        self.assertNotIn("sine_start", device.protocol_actions)
        self.assertEqual(device.recording_state_when_open_commanded, RecordingState.RECORDING)
        self.assertEqual(device.initial_angle_at_latch_open, 14.8)
        self.assertIsNotNone(device.manifest_at_latch_open)
        self.assertEqual(
            device.manifest_at_latch_open["experiment"]["measured_initial_angle_deg"],
            14.8,
        )
        self.assertEqual(
            device.manifest_at_latch_open["name"],
            "Trial_A_VS_A5mm_F1.8Hz_Thetap14.8deg_T30s",
        )
        device.release_latch_open()
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )
        run = controller.experiment_run.snapshot
        self.assertEqual(run.experiment_id, experiment_id)
        self.assertEqual(run.measured_initial_angle_deg, 14.8)
        self.assertEqual(
            run.generated_name,
            "Trial_A_VS_A5mm_F1.8Hz_Thetap14.8deg_T30s",
        )
        self.assertEqual(
            [action for action in device.protocol_actions if action in ("latch_open", "sine_start")],
            ["latch_open", "sine_start"],
        )
        self.assertTrue(device.sine_active)
        self.assertEqual(device.sine_amplitude_mm, 5.0)
        self.assertEqual(device.sine_frequency_hz, 1.8)
        session = controller.experiment_recorder.active_session
        self.assertIsNotNone(session)
        self.assertEqual(session.manifest["name"], run.generated_name)
        self.assertEqual(session.manifest["experiment"]["measured_initial_angle_deg"], 14.8)
        self.assertEqual(session.manifest["experiment"]["commanded_amplitude_mm"], 5.0)
        self.assertEqual(session.manifest["experiment"]["commanded_frequency_hz"], 1.8)
        self.assertEqual(session.manifest["experiment"]["requested_duration_s"], 30.0)
        self.assertNotIn("latch_hold", device.protocol_actions)
        self.assertNotIn("target_initial_angle_deg", session.manifest["experiment"])
        self.assertTrue((repository.root / experiment_id / "serial_capture.log").exists())
        self.assertTrue(self.wait_for(lambda: controller.experiment_run.snapshot.sample_count > 0))
        self.assertGreater(controller.experiment_run._samples_at_start, 0)

        controller.experiment_run.stop()
        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.OPERATOR_STOPPED
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertFalse(device.sine_active)
        self.assertEqual(device.latch_state, "open")
        self.assertNotIn("latch_hold", device.protocol_actions)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "INTERRUPTED")
        self.assertEqual(manifest["experiment"]["completion_reason"], "operator_stop")
        self.assertEqual(manifest["experiment"]["measured_initial_angle_deg"], 14.8)
        self.assertNotIn("target_initial_angle_deg", manifest["experiment"])

    def test_preflight_rejects_frequency_and_unprovable_out_of_travel_amplitude(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)

        with self.assertRaisesRegex(ValueError, "firmware command limit"):
            controller.experiment_run.start(VerticalSineDefinition(5.0, 10.1, 30.0))
        with self.assertRaisesRegex(ValueError, "homed travel"):
            controller.experiment_run.start(VerticalSineDefinition(15.0, 1.8, 30.0))
        self.assertFalse(controller.experiment_recorder.is_recording)
        self.assertEqual(device.latch_state, "hold")

    def test_zero_amplitude_run_enters_running_and_completes_by_duration(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)

        experiment_id = controller.experiment_run.start(VerticalSineDefinition(0.0, 1.8, 0.25))

        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )
        self.assertTrue(device.sine_active)
        self.assertEqual(device.sine_amplitude_mm, 0.0)
        self.assertEqual(device.sine_frequency_hz, 1.8)
        self.assertEqual(controller.state.snapshot.recording, RecordingState.RECORDING)
        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.COMPLETE
                and controller.state.snapshot.recording == RecordingState.SAVED
            )
        )
        self.assertFalse(device.sine_active)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "COMPLETED")
        self.assertEqual(manifest["experiment"]["commanded_amplitude_mm"], 0.0)
        self.assertEqual(manifest["experiment"]["commanded_frequency_hz"], 1.8)

    def test_zero_amplitude_run_can_be_stopped_by_operator(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        experiment_id = controller.experiment_run.start(VerticalSineDefinition(0.0, 1.8, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )

        controller.experiment_run.stop()

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.OPERATOR_STOPPED
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertFalse(device.sine_active)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "INTERRUPTED")
        self.assertEqual(manifest["experiment"]["completion_reason"], "operator_stop")

    def test_autoresonance_transaction_uses_correlated_commands_and_preserves_parameters(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        device.protocol_actions.clear()

        controller.experiment_run.validate_start(AutoResonanceDefinition(10.0, 0.0, 20.0))
        with self.assertRaisesRegex(ValueError, "half-travel"):
            controller.experiment_run.start(AutoResonanceDefinition(15.0, 0.0, 20.0))
        self.assertFalse(controller.experiment_recorder.is_recording)
        self.assertNotIn("parametric_start", device.protocol_actions)

        definition = AutoResonanceDefinition(6.0, -12.5, 0.2, label="AR test", notes="phase sign")
        experiment_id = controller.experiment_run.start(definition)
        self.assertTrue(
            self.wait_for(lambda: device.latch_state == "open" and "parametric_start" in device.protocol_actions)
        )
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )
        self.assertTrue(device.autores_active)
        self.assertEqual(device.autores_amplitude_mm, 6.0)
        self.assertEqual(device.autores_phase_deg, -12.5)
        self.assertNotIn("sine_start", device.protocol_actions)
        self.assertEqual(device.latch_state, "open")
        self.assertEqual(device.recording_state_when_open_commanded, RecordingState.RECORDING)
        self.assertEqual(device.initial_angle_at_latch_open, 14.8)
        self.assertEqual(
            [action for action in device.protocol_actions if action in ("latch_open", "parametric_start")],
            ["latch_open", "parametric_start"],
        )

        manifest_before_stop = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(
            manifest_before_stop["experiment"]["commanded_amplitude_mm"],
            6.0,
        )
        self.assertEqual(manifest_before_stop["experiment"]["commanded_phase_deg"], -12.5)
        self.assertEqual(manifest_before_stop["experiment"]["experiment_mode"],
                         "AUTO_RESONANCE_PARAMETRIC")

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.COMPLETE
                and controller.state.snapshot.recording == RecordingState.SAVED
            )
        )
        self.assertFalse(device.autores_active)
        self.assertIn("parametric_stop", device.protocol_actions)
        self.assertEqual(device.latch_state, "open")
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "COMPLETED")
        self.assertEqual(manifest["experiment"]["completion_reason"], "duration_elapsed")

    def test_phase_tracked_sine_uses_its_own_correlated_lifecycle_and_metadata(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        device.protocol_actions.clear()

        definition = PhaseTrackedSineDefinition(5.0, 20.0, 0.15, label="phase run", notes="short replay")
        experiment_id = controller.experiment_run.start(definition)
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )
        self.assertTrue(device.phase_tracked_sine_active)
        self.assertFalse(device.autores_active)
        self.assertFalse(device.sine_active)
        self.assertEqual(device.autores_amplitude_mm, 5.0)
        self.assertEqual(device.autores_phase_deg, 20.0)
        self.assertEqual(
            [
                command
                for command in device.protocol_actions
                if command in ("latch_open", "phase_sine_start", "autores_start", "sine_start")
            ],
            ["latch_open", "phase_sine_start"],
        )

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.COMPLETE
                and controller.state.snapshot.recording == RecordingState.SAVED
            )
        )
        self.assertFalse(device.phase_tracked_sine_active)
        self.assertIn("phase_sine_stop", device.protocol_actions)
        self.assertNotIn("autores_stop", device.protocol_actions)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(
            manifest["experiment"]["experiment_mode"],
            "PHASE_TRACKED_SINE_AUTO_RESONANCE",
        )
        self.assertEqual(manifest["experiment"]["commanded_amplitude_mm"], 5.0)
        self.assertEqual(manifest["experiment"]["commanded_phase_deg"], 20.0)
        self.assertEqual(manifest["experiment"]["requested_duration_s"], 0.15)

    def test_typed_autoresonance_commands_validate_and_correlate_parameters(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        commands = controller.firmware_commands

        with self.assertRaisesRegex(ValueError, "7.5 mm"):
            commands.start_parametric_closed_loop(10.0, 0.0)
        with self.assertRaisesRegex(ValueError, "greater than zero"):
            commands.start_parametric_closed_loop(-1.0, 0.0)
        with self.assertRaisesRegex(ValueError, "finite"):
            commands.start_parametric_closed_loop(5.0, float("nan"))
        self.assertNotIn("autores_start", device.protocol_actions)

        commands.start_parametric_closed_loop(6.25, -17.5)
        self.assertTrue(
            self.wait_for(
                lambda: device.autores_active
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(device.autores_amplitude_mm, 6.25)
        self.assertEqual(device.autores_phase_deg, -17.5)
        request_line = next(
            write.decode("ascii").strip()
            for write in device.writes
            if b"autores_start" in write
        )
        self.assertRegex(request_line, r"maint \d+ autores_start 6\.25 -17\.5")

        commands.stop_parametric_closed_loop()
        self.assertTrue(
            self.wait_for(
                lambda: not device.autores_active
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(device.protocol_actions[-1], "autores_stop")

    def test_proven_parametric_command_matches_putty_operating_point_exactly(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)

        controller.firmware_commands.start_parametric(7.5, 22.5)

        self.assertTrue(
            self.wait_for(
                lambda: device.autores_active
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(device.autores_amplitude_mm, 7.5)
        self.assertEqual(device.autores_phase_deg, 22.5)
        request_line = next(
            write.decode("ascii").strip()
            for write in device.writes
            if b"parametric_start" in write
        )
        self.assertRegex(request_line, r"maint \d+ parametric_start 7\.5 22\.5")

    def test_parametric_stop_response_receipt_survives_delayed_gui_dispatch_and_interleaved_lines(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        commands = controller.firmware_commands
        completed = []
        failed = []
        commands.command_completed.connect(completed.append)
        commands.command_failed.connect(failed.append)

        request_id = commands.stop_parametric()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not (
            controller.serial_client.command_was_written(str(request_id))
            and controller.serial_client.has_maintenance_response(request_id)
        ):
            time.sleep(0.003)

        self.assertTrue(controller.serial_client.command_was_written(str(request_id)))
        self.assertTrue(controller.serial_client.has_maintenance_response(request_id))
        commands._on_response(
            MaintenanceResponse(
                request_id + 1,
                "parametric_stop",
                {"ok": "1", "active": "0", "active_mode": "idle"},
                "wrong request id",
            )
        )
        self.assertTrue(commands.is_busy)

        commands._on_timeout()

        self.assertFalse(commands.is_busy)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0][0], FirmwareCommand.PARAMETRIC_STOP)
        self.assertEqual(failed, [])
        self.assertIn("parametric_stop", device.protocol_actions)
        commands._on_response(
            MaintenanceResponse(
                request_id,
                "parametric_stop",
                {"ok": "1", "active": "0", "active_mode": "idle"},
                "duplicate response",
            )
        )
        self.assertEqual(len(completed), 1)
        self.assertEqual(failed, [])

    def test_missing_parametric_stop_response_still_times_out_truthfully(self) -> None:
        directory = self.temporary_directory()
        controller, _device, _repository = self.create_connected_controller(
            directory,
            suppress_parametric_stop_response=True,
        )
        self.addCleanup(controller.shutdown)
        commands = controller.firmware_commands
        failures = []
        commands.command_failed.connect(failures.append)

        request_id = commands.stop_parametric()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not controller.serial_client.command_was_written(
            str(request_id)
        ):
            time.sleep(0.003)
        self.assertTrue(controller.serial_client.command_was_written(str(request_id)))

        commands._on_timeout()
        self.assertTrue(commands.is_busy)
        commands._on_timeout()

        self.assertFalse(commands.is_busy)
        self.assertEqual(len(failures), 1)
        self.assertIn("response", failures[0].message)

    def test_autoresonance_operator_stop_uses_correlated_stop_and_keeps_latch_open(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        experiment_id = controller.experiment_run.start(AutoResonanceDefinition(5.0, 0.0, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )

        controller.experiment_run.stop()

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.OPERATOR_STOPPED
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertFalse(device.autores_active)
        self.assertIn("parametric_stop", device.protocol_actions)
        self.assertNotIn("sine_stop", device.protocol_actions)
        self.assertEqual(device.latch_state, "open")
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["experiment"]["completion_reason"], "operator_stop")

    def test_autoresonance_firmware_fault_stops_and_finalizes_run(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        experiment_id = controller.experiment_run.start(AutoResonanceDefinition(5.0, 0.0, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )

        device.report_closed_loop_fault()

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.FAULT
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertFalse(device.autores_active)
        self.assertIn("parametric_stop", device.protocol_actions)
        self.assertEqual(device.latch_state, "open")
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "INTERRUPTED")
        self.assertTrue(manifest["experiment"]["completion_reason"].startswith("safety_event:fault_appeared"))

    def test_pendulum_angle_fault_reason_survives_stop_finalization_without_repositioning(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        experiment_id = controller.experiment_run.start(
            AutoResonanceDefinition(7.5, 22.5, 30.0)
        )
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )
        home_commands_before_fault = device.protocol_actions.count("home")

        device.report_closed_loop_fault("pendulum_angle_limit")

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.FAULT
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertIn("parametric_stop", device.protocol_actions)
        self.assertEqual(device.latch_state, "open")
        self.assertEqual(device.protocol_actions.count("home"), home_commands_before_fault)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(
            manifest["experiment"]["completion_reason"],
            "fault_appeared:pendulum_angle_limit",
        )

    def test_autoresonance_lower_limit_fault_code_is_preserved(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        experiment_id = controller.experiment_run.start(AutoResonanceDefinition(5.0, 0.0, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )

        device.report_closed_loop_fault("lower_limit")

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.FAULT
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertIn("parametric_stop", device.protocol_actions)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(
            manifest["experiment"]["completion_reason"],
            "safety_event:fault_appeared:lower_limit",
        )

    def test_autoresonance_transient_sensor_events_wait_for_firmware_fault_threshold(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        event_log = []
        controller.state.event_added.connect(event_log.append)
        experiment_id = controller.experiment_run.start(AutoResonanceDefinition(5.0, 5.0, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )

        device.report_angle_sensor_unavailable()

        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.latest_sample is not None
                and controller.state.snapshot.latest_sample.angle_deg is None
                and any(
                    event.severity == EventSeverity.WARNING
                    and "AS5600 communication degraded" in event.message
                    for event in event_log
                )
            )
        )
        self.assertEqual(controller.experiment_run.snapshot.state, ExperimentRunState.RUNNING)
        self.assertEqual(controller.state.snapshot.recording, RecordingState.RECORDING)
        self.assertTrue(device.autores_active)
        self.assertNotIn("parametric_stop", device.protocol_actions)

        device.report_angle_sensor_restored()
        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.latest_sample is not None
                and controller.state.snapshot.latest_sample.angle_deg is not None
                and any(
                    event.severity == EventSeverity.INFO
                    and "AS5600 communication restored during the active experiment" in event.message
                    for event in event_log
                )
            )
        )
        self.assertEqual(controller.experiment_run.snapshot.state, ExperimentRunState.RUNNING)
        self.assertTrue(device.autores_active)
        self.assertNotIn("autores_stop", device.protocol_actions)

        device.report_closed_loop_fault("angle_sensor")
        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.FAULT
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertIn("parametric_stop", device.protocol_actions)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(
            manifest["experiment"]["completion_reason"],
            "safety_event:fault_appeared:angle_sensor",
        )

    def test_autoresonance_connection_loss_interrupts_recording(self) -> None:
        directory = self.temporary_directory()
        controller, _device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, _device)
        controller.experiment_run.start(AutoResonanceDefinition(5.0, 0.0, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )

        controller.state.set_connection(ConnectionState.DISCONNECTED)

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.INTERRUPTED
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )

    def test_autoresonance_start_remains_blocked_by_latched_firmware_fault(self) -> None:
        directory = self.temporary_directory()
        controller, _device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, _device)
        controller.state.set_hardware_status(
            HardwareStatus(fault="Firmware fault 3", fault_known=True)
        )

        with self.assertRaisesRegex(RuntimeError, "System Check and Home|blocking firmware fault"):
            controller.experiment_run.start(AutoResonanceDefinition(5.0, 0.0, 30.0))

        self.assertFalse(controller.experiment_recorder.is_recording)

    def test_serial_disconnect_during_run_still_interrupts_transaction(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )

        controller.state.set_connection(ConnectionState.DISCONNECTED)

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.INTERRUPTED
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )

    def test_as5600_loss_and_recovery_during_vertical_sine_run_are_logged_without_stopping(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        event_log = []
        controller.state.event_added.connect(event_log.append)
        window = MainWindow(controller)
        self.addCleanup(window.close)
        page = window.experiment_page
        experiment_id = controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 0.9))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.omega_est_deg_s is not None)
        )

        device.report_angle_sensor_unavailable()

        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.latest_sample is not None
                and controller.state.snapshot.latest_sample.angle_deg is None
                and controller.experiment_run.snapshot.angle_source == AngleSource.PREDICTED
                and any(
                    event.severity == EventSeverity.WARNING
                    and "AS5600 communication degraded" in event.message
                    for event in event_log
                )
            )
        )
        self.assertEqual(controller.experiment_run.snapshot.state, ExperimentRunState.RUNNING)
        self.assertEqual(controller.state.snapshot.recording, RecordingState.RECORDING)
        self.assertTrue(device.sine_active)
        self.assertIsNone(controller.state.snapshot.latest_sample.angle_deg)
        self.assertAlmostEqual(controller.experiment_run.snapshot.predicted_angle_deg, 14.8)
        self.assertIn("Angle source: PREDICTED", page.angle_source_value.text())
        self.assertEqual(device.sine_amplitude_mm, 5.0)

        device.report_angle_sensor_restored()
        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.latest_sample is not None
                and controller.state.snapshot.latest_sample.angle_deg is not None
                and controller.experiment_run.snapshot.angle_source == AngleSource.MEASURED
                and controller.experiment_run.snapshot.prediction_recovery_error_deg is not None
                and any(
                    event.severity == EventSeverity.INFO
                    and "AS5600 communication restored during open-loop Vertical Sine" in event.message
                    for event in event_log
                )
            )
        )
        self.assertEqual(controller.experiment_run.snapshot.state, ExperimentRunState.RUNNING)
        self.assertTrue(device.sine_active)
        self.assertEqual(page.angle_source_value.text(), "Angle source: MEASURED")
        self.assertIsNotNone(controller.experiment_run.snapshot.prediction_outage_duration_ms)
        self.assertGreater(controller.experiment_run.snapshot.prediction_outage_duration_ms, 0.0)
        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.COMPLETE
                and controller.state.snapshot.recording == RecordingState.SAVED
            )
        )
        self.assertFalse(device.sine_active)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "COMPLETED")
        derived_path = repository.root / experiment_id / "derived" / "angle_prediction.csv"
        self.assertTrue(derived_path.exists())
        with derived_path.open("r", encoding="utf-8", newline="") as derived_file:
            derived_rows = list(csv.DictReader(derived_file))
        self.assertTrue(any(row["angle_source"] == "predicted" for row in derived_rows))
        self.assertTrue(
            any(row["prediction_recovery_error_deg"] for row in derived_rows)
        )
        self.assertNotIn("angle_deg", derived_rows[0])
        with (repository.root / experiment_id / "telemetry.csv").open(
            "r", encoding="utf-8", newline=""
        ) as telemetry_file:
            telemetry_rows = list(csv.DictReader(telemetry_file))
        self.assertTrue(
            any(
                row["quality"] == "missing_measurement" and row["angle_deg"] == ""
                for row in telemetry_rows
            )
        )
        raw_capture_path = Path(controller.state.snapshot.raw_capture_path)
        controller.disconnect_real()
        self.assertTrue(
            self.wait_for(
                lambda: device.closed
                and controller.state.snapshot.connection == ConnectionState.DISCONNECTED
            )
        )
        raw_capture = raw_capture_path.read_text(encoding="utf-8")
        raw_payloads = "\n".join(
            base64.b64decode(json.loads(line)["raw_bytes_base64"]).decode("ascii")
            for line in raw_capture.splitlines()
        )
        self.assertIn("event: type=angle_sensor_unavailable", raw_payloads)
        self.assertIn("event: type=angle_sensor_restored", raw_payloads)
        self.assertIn("angle_deg=NaN", raw_payloads)

    def test_live_predictor_uses_device_sample_time_not_irregular_host_receive_time(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )
        device._live_enabled = False
        predictor = controller.experiment_run._angle_predictor
        predictor.reset()

        for sample in (
            TelemetrySample(
                100,
                10.0,
                10.0,
                0.0,
                source="real",
                device_time_s=10.0,
                received_elapsed_s=1.0,
            ),
            TelemetrySample(
                101,
                10.1,
                11.0,
                0.0,
                source="real",
                device_time_s=10.1,
                received_elapsed_s=3.7,
            ),
            TelemetrySample(
                102,
                10.2,
                None,
                0.0,
                source="real",
                device_time_s=10.2,
                received_elapsed_s=11.0,
            ),
        ):
            controller.experiment_run._on_telemetry_sample(sample)

        snapshot = controller.experiment_run.snapshot
        self.assertAlmostEqual(snapshot.omega_est_deg_s, 10.0)
        self.assertEqual(snapshot.angle_source, AngleSource.PREDICTED)
        self.assertAlmostEqual(snapshot.predicted_angle_deg, 12.0)
        self.assertAlmostEqual(snapshot.prediction_age_ms, 100.0)
        controller.experiment_run.stop()
        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.OPERATOR_STOPPED
            )
        )

    def test_open_failure_preserves_recording_and_never_starts_sine(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        device.reject_latch_open = True
        device.defer_latch_open = True

        experiment_id = controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 30.0))
        self.assertTrue(self.wait_for(lambda: device._pending_open is not None))
        controller.state.publish_sample(
            TelemetrySample(
                9000,
                123.4,
                14.8,
                0.25,
                source="real",
                device_time_s=123.4,
                device_sample_index=9000,
            )
        )
        self.assertTrue(self.wait_for(lambda: controller.experiment_recorder.active_sample_count > 0))
        device.release_latch_open()

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.FAULT
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertNotIn("sine_start", device.protocol_actions)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "INTERRUPTED")
        self.assertEqual(manifest["experiment"]["completion_reason"].split(":", 1)[0], "latch_open_failed")
        self.assertGreater(manifest["sample_count"], 0)

    def test_operator_stop_during_preparing_waits_for_open_ack_then_skips_sine(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        device.defer_latch_open = True
        experiment_id = controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 30.0))
        self.assertTrue(self.wait_for(lambda: device._pending_open is not None))

        controller.experiment_run.stop()
        self.assertEqual(controller.experiment_run.snapshot.state, ExperimentRunState.STOPPING)
        self.assertNotIn("sine_start", device.protocol_actions)
        device.release_latch_open()

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.OPERATOR_STOPPED
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertNotIn("sine_start", device.protocol_actions)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "INTERRUPTED")
        self.assertEqual(manifest["experiment"]["completion_reason"], "operator_stop")

    def test_sine_start_rejection_stops_safely_and_finalizes_interrupted(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(
            directory,
            reject_sine_start=True,
        )
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)

        experiment_id = controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 30.0))

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.FAULT
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertFalse(device.sine_active)
        self.assertIn("sine_stop", device.protocol_actions)
        self.assertEqual(
            repository.load(repository.root / experiment_id).manifest["completion_status"],
            "INTERRUPTED",
        )

    def test_duration_expiry_stops_and_marks_complete(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        experiment_id = controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 0.15))

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.COMPLETE
                and controller.state.snapshot.recording == RecordingState.SAVED,
                timeout_s=4.0,
            )
        )
        self.assertFalse(device.sine_active)
        self.assertEqual(device.latch_state, "open")
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "COMPLETED")
        self.assertEqual(manifest["experiment"]["completion_reason"], "duration_elapsed")
        self.assertGreaterEqual(manifest["experiment"]["actual_elapsed_duration_s"], 0.15)

    def test_firmware_limit_stop_interrupts_run_instead_of_marking_complete(self) -> None:
        directory = self.temporary_directory()
        controller, device, repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        experiment_id = controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )

        device.stop_sine_by_limit()

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.INTERRUPTED
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertIn("sine_stop", device.protocol_actions)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "INTERRUPTED")
        self.assertEqual(manifest["experiment"]["completion_reason"], "sine_motion_stopped:limit")

    def test_disconnect_requests_run_stop_before_closing_serial_transport(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 30.0))
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )

        controller.disconnect_real()

        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.INTERRUPTED
                and controller.state.snapshot.recording == RecordingState.INTERRUPTED
            )
        )
        self.assertFalse(device.closed)
        self.assertEqual(controller.state.snapshot.connection, ConnectionState.CONNECTED)
        self.assertIn("sine_stop", device.protocol_actions)

        controller.disconnect_real()
        self.assertTrue(
            self.wait_for(
                lambda: device.closed
                and controller.state.snapshot.connection == ConnectionState.DISCONNECTED
            )
        )

    def test_experiment_page_has_typed_setup_and_one_stateful_run_button(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        window = MainWindow(controller)
        page = window.experiment_page

        self.assertEqual(page.experiment_mode_selector.count(), 3)
        self.assertEqual(page.experiment_mode_selector.currentText(), "Vertical Sinusoidal Excitation")
        self.assertTrue(all(hasattr(page, name) for name in (
            "amplitude_input",
            "frequency_input",
            "duration_input",
            "experiment_name_input",
            "experiment_notes_input",
        )))
        self.assertFalse(hasattr(page, "initial_angle_input"))
        self.assertFalse(hasattr(page, "start_recording_button"))
        self.assertFalse(hasattr(page, "stop_recording_button"))
        self.assertFalse(page.primary_run_button.isEnabled())
        self.assertEqual(page.primary_run_button.text(), "RUN EXPERIMENT")
        self.assertEqual(device.protocol_actions.count("latch_open"), 1)
        self.assertFalse(page.prepare_latch_button.isEnabled())

        page.amplitude_input.setText("5.0")
        page.frequency_input.setText("1.8")
        page.duration_input.setText("30")
        page.experiment_name_input.setText("UI Trial")
        page.run_error_value.setText("")
        self.assertTrue(page.primary_run_button.isEnabled())
        page.primary_run_button.click()

        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )
        self.assertEqual(page.primary_run_button.text(), "STOP EXPERIMENT")
        self.assertFalse(page.amplitude_input.isEnabled())
        self.assertFalse(page.frequency_input.isEnabled())
        self.assertFalse(page.duration_input.isEnabled())

        page.primary_run_button.click()
        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.OPERATOR_STOPPED
            )
        )
        self.assertEqual(page.primary_run_button.text(), "RUN EXPERIMENT")
        window.close()

    def test_autoresonance_mode_defaults_fields_and_restores_vertical_sine_inputs(self) -> None:
        directory = self.temporary_directory()
        controller, _device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, _device)
        window = MainWindow(controller)
        self.addCleanup(window.close)
        page = window.experiment_page

        page.amplitude_input.setText("5.0")
        page.frequency_input.setText("1.8")
        page.duration_input.setText("20")
        page.experiment_mode_selector.setCurrentIndex(1)

        self.assertEqual(page.experiment_mode_selector.currentText(), "Auto-Resonance")
        self.assertEqual(page.amplitude_input.text(), "7.5")
        self.assertEqual(page.phase_input.text(), "22.5")
        self.assertEqual(page.duration_input.text(), "20")
        self.assertTrue(page.frequency_input.isHidden())
        self.assertFalse(page.phase_input.isHidden())
        self.assertTrue(page.primary_run_button.isEnabled())
        self.assertEqual(page._definition_from_fields().amplitude_mm, 7.5)
        self.assertEqual(page._definition_from_fields().phase_deg, 22.5)

        page.amplitude_input.setText("6.0")
        page.phase_input.setText("-12.5")
        self.assertTrue(page.primary_run_button.isEnabled())
        self.assertAlmostEqual(page._definition_from_fields().phase_deg, -12.5)

        page.experiment_mode_selector.setCurrentIndex(0)
        self.assertEqual(page.amplitude_input.text(), "5.0")
        self.assertEqual(page.frequency_input.text(), "1.8")
        self.assertFalse(page.frequency_input.isHidden())
        self.assertTrue(page.phase_input.isHidden())
        self.assertTrue(page.primary_run_button.isEnabled())

        page.experiment_mode_selector.setCurrentIndex(1)
        self.assertEqual(page.amplitude_input.text(), "7.5")
        self.assertEqual(page.phase_input.text(), "22.5")
        self.assertTrue(page.primary_run_button.isEnabled())

    def test_phase_tracked_sine_mode_has_no_frequency_and_uses_phase_defaults(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, device)
        window = MainWindow(controller)
        self.addCleanup(window.close)
        page = window.experiment_page

        page.amplitude_input.setText("4.0")
        page.frequency_input.setText("1.7")
        page.duration_input.setText("12")
        page.experiment_mode_selector.setCurrentIndex(2)

        self.assertEqual(
            page.experiment_mode_selector.currentText(),
            "Phase-Tracked Sinusoidal Auto-Resonance",
        )
        self.assertEqual(page.amplitude_input.text(), "10.0")
        self.assertEqual(page.phase_input.text(), "0.0")
        self.assertEqual(page.duration_input.text(), "12")
        self.assertTrue(page.frequency_input.isHidden())
        self.assertFalse(page.phase_input.isHidden())
        self.assertFalse(page.primary_run_button.isEnabled())
        self.assertIn("7.5 mm", page.run_error_value.text())

        page.amplitude_input.setText("5.0")
        page.phase_input.setText("25")
        definition = page._definition_from_fields()
        self.assertIsInstance(definition, PhaseTrackedSineDefinition)
        self.assertEqual(definition.phase_deg, 25.0)
        self.assertTrue(page.primary_run_button.isEnabled())

        page.experiment_mode_selector.setCurrentIndex(0)
        self.assertEqual(page.amplitude_input.text(), "4.0")
        self.assertEqual(page.frequency_input.text(), "1.7")
        self.assertTrue(page.phase_input.isHidden())

    def test_experiment_page_accepts_zero_amplitude_baseline(self) -> None:
        directory = self.temporary_directory()
        controller, _device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        self.prepare_ready_held_system(controller, _device)
        window = MainWindow(controller)
        self.addCleanup(window.close)
        page = window.experiment_page

        page.amplitude_input.setText("0")
        page.frequency_input.setText("1.8")
        page.duration_input.setText("3")

        self.assertTrue(page.primary_run_button.isEnabled())
        self.assertIn("Stationary pivot baseline", page.run_name_preview.text())
        self.assertNotIn("pivot amplitude must", page.run_error_value.text())

    def test_experiment_setup_inputs_have_readable_minimum_heights(self) -> None:
        directory = self.temporary_directory()
        controller, _device, _repository = self.create_connected_controller(directory)
        self.addCleanup(controller.shutdown)
        window = MainWindow(controller)
        self.addCleanup(window.close)
        page = window.experiment_page

        for control in (
            page.experiment_mode_selector,
            page.amplitude_input,
            page.frequency_input,
            page.duration_input,
            page.experiment_name_input,
            page.experiment_notes_input,
        ):
            with self.subTest(control=control.objectName()):
                self.assertGreaterEqual(control.minimumHeight(), 36)

    def test_prepare_latch_button_is_disabled_when_disconnected(self) -> None:
        controller = WorkbenchController()
        self.addCleanup(controller.shutdown)
        controller.select_data_source(DataSourceMode.REAL)
        window = MainWindow(controller)
        self.addCleanup(window.close)

        self.assertEqual(controller.state.snapshot.connection, ConnectionState.DISCONNECTED)
        self.assertFalse(window.experiment_page.prepare_latch_button.isEnabled())

    def test_experiment_page_prepares_shared_latch_without_starting_run(self) -> None:
        directory = self.temporary_directory()
        controller, device, _repository = self.create_connected_controller(
            directory,
            initially_homed=True,
            defer_latch_hold=True,
        )
        self.addCleanup(controller.shutdown)
        controller.maintenance.run_system_check()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.bit_state == BitResultState.PASSED
                and not controller.firmware_commands.is_busy
            )
        )
        window = MainWindow(controller)
        self.addCleanup(window.close)
        page = window.experiment_page
        maintenance_page = window.maintenance_page

        self.assertEqual(page.latch_state_value.text(), "Pendulum Latch: UNKNOWN")
        page.amplitude_input.setText("5.0")
        page.frequency_input.setText("1.8")
        page.duration_input.setText("30")
        self.assertFalse(page.primary_run_button.isEnabled())
        self.assertTrue(page.prepare_latch_button.isEnabled())

        page.prepare_latch_button.click()
        self.assertTrue(self.wait_for(lambda: device._pending_hold is not None))
        self.assertEqual(
            controller.maintenance.snapshot.latch_state,
            PendulumLatchState.COMMANDING_HOLD,
        )
        self.assertEqual(page.latch_state_value.text(), "Pendulum Latch: COMMANDING HOLD")
        self.assertFalse(page.prepare_latch_button.isEnabled())
        self.assertFalse(page.primary_run_button.isEnabled())
        self.assertEqual(device.latch_commands, ["latch_hold"])
        self.assertNotIn("sine_start", device.protocol_actions)
        self.assertFalse(controller.experiment_recorder.is_recording)

        device.release_latch_hold()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.HOLD
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(page.latch_state_value.text(), "Pendulum Latch: HOLD")
        self.assertEqual(maintenance_page.latch_state_value.text(), PendulumLatchState.HOLD.value)
        self.assertEqual(page.prepare_latch_button.text(), "HOLD CONFIRMED")
        self.assertFalse(page.prepare_latch_button.isEnabled())
        self.assertTrue(page.primary_run_button.isEnabled())
        self.assertNotIn("sine_start", device.protocol_actions)
        self.assertFalse(controller.experiment_recorder.is_recording)

        device.defer_latch_open = True
        page.primary_run_button.click()
        self.assertTrue(self.wait_for(lambda: device._pending_open is not None))
        self.assertEqual(controller.experiment_run.snapshot.state, ExperimentRunState.PREPARING)
        self.assertEqual(page.latch_state_value.text(), "Pendulum Latch: COMMANDING OPEN")
        self.assertEqual(
            maintenance_page.latch_state_value.text(),
            PendulumLatchState.COMMANDING_OPEN.value,
        )
        self.assertFalse(page.prepare_latch_button.isEnabled())
        self.assertEqual(page.primary_run_button.text(), "STOP EXPERIMENT")
        self.assertTrue(page.primary_run_button.isEnabled())
        self.assertNotIn("sine_start", device.protocol_actions)

        device.release_latch_open()
        self.assertTrue(
            self.wait_for(lambda: controller.experiment_run.snapshot.state == ExperimentRunState.RUNNING)
        )
        self.assertEqual(page.latch_state_value.text(), "Pendulum Latch: OPEN")
        self.assertEqual(maintenance_page.latch_state_value.text(), PendulumLatchState.OPEN.value)
        self.assertFalse(page.prepare_latch_button.isEnabled())

        page.primary_run_button.click()
        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.OPERATOR_STOPPED
            )
        )
        controller.state.set_motion(MotionState.RUNNING)
        self.assertFalse(page.prepare_latch_button.isEnabled())


if __name__ == "__main__":
    unittest.main()
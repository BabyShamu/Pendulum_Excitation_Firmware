import base64
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.experiment_run import ExperimentRunState, VerticalSineDefinition
from pendulum_workbench.domain.maintenance import BitResultState, PendulumLatchState, ReadinessState
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    HomeStatus,
    MotionState,
    RecordingState,
)
from pendulum_workbench.domain.models import TelemetrySample
from pendulum_workbench.experiments.repository import ExperimentRepository
from pendulum_workbench.infrastructure.serial_client import SerialTelemetryClient
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
        **kwargs: object,
    ) -> None:
        del port, baud_rate, kwargs
        self.angle_available = angle_available
        self.reject_latch_open = reject_latch_open
        self.reject_sine_start = reject_sine_start
        self.defer_latch_hold = defer_latch_hold
        self.defer_latch_open = defer_latch_open
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
                mode = "sine" if self.sine_active else "idle"
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
        experiment_id = controller.experiment_run.start(VerticalSineDefinition(5.0, 1.8, 0.45))
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
        self.assertTrue(device.sine_active)

        device.report_angle_sensor_restored()
        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.latest_sample is not None
                and controller.state.snapshot.latest_sample.angle_deg is not None
                and any(
                    event.severity == EventSeverity.INFO
                    and "AS5600 communication restored during open-loop Vertical Sine" in event.message
                    for event in event_log
                )
            )
        )
        self.assertEqual(controller.experiment_run.snapshot.state, ExperimentRunState.RUNNING)
        self.assertTrue(device.sine_active)
        self.assertTrue(
            self.wait_for(
                lambda: controller.experiment_run.snapshot.state == ExperimentRunState.COMPLETE
                and controller.state.snapshot.recording == RecordingState.SAVED
            )
        )
        self.assertFalse(device.sine_active)
        manifest = repository.load(repository.root / experiment_id).manifest
        self.assertEqual(manifest["completion_status"], "COMPLETED")
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

        self.assertEqual(page.experiment_mode_selector.count(), 1)
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
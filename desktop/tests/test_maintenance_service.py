import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.application.firmware_commands import FirmwareCommandService
from pendulum_workbench.application.maintenance_service import MaintenanceService
from pendulum_workbench.domain.maintenance import (
    BitResultState,
    CheckName,
    CheckState,
    ManualMotionState,
    PendulumLatchState,
    ReadinessState,
)
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    HardwareStatus,
    HomeStatus,
    MotionState,
    RecordingState,
    TelemetrySample,
)
from pendulum_workbench.infrastructure.serial_client import SerialTelemetryClient
from pendulum_workbench.infrastructure.firmware_protocol import DeviceEvent, FirmwareProtocolParser
from pendulum_workbench.ui.main_window import MainWindow
from pendulum_workbench.ui.pages.maintenance_page import MaintenancePage


class MaintenanceFakeSerial:
    def __init__(
        self,
        port: str,
        baud_rate: int,
        *,
        fault_code: int = 0,
        initially_homed: bool = False,
        home_succeeds: bool = True,
        angle_available: bool = True,
        defer_status: bool = False,
        defer_home: bool = False,
        defer_manual_start: bool = False,
        defer_latch_commands: bool = False,
        reject_latch_commands: bool = False,
        prepend_stale_response: bool = False,
        manual_deadman_timeout_s: float = 0.35,
        drop_manual_keepalives: bool = False,
        drop_manual_stop: bool = False,
        upper_active: bool = False,
        lower_active: bool = False,
        **kwargs: object,
    ) -> None:
        del port, baud_rate, kwargs
        self.fault_code = fault_code
        self.homed = initially_homed
        self.home_succeeds = home_succeeds
        self.home_motion_start_count = 0
        self.angle_available = angle_available
        self.defer_status = defer_status
        self.defer_home = defer_home
        self.defer_manual_start = defer_manual_start
        self.defer_latch_commands = defer_latch_commands
        self.reject_latch_commands = reject_latch_commands
        self.prepend_stale_response = prepend_stale_response
        self.manual_deadman_timeout_s = manual_deadman_timeout_s
        self.drop_manual_keepalives = drop_manual_keepalives
        self.drop_manual_stop = drop_manual_stop
        self.upper_active = upper_active
        self.lower_active = lower_active
        self.manual_active = False
        self.manual_direction: str | None = None
        self.manual_started_at = 0.0
        self.last_manual_keepalive = 0.0
        self.manual_keepalive_received: list[tuple[str, float, bool]] = []
        self.pending_status_id: str | None = None
        self.pending_home_id: str | None = None
        self.pending_manual_start: tuple[str, str] | None = None
        self.pending_latch_command: tuple[str, str] | None = None
        self.latch_state = "unknown"
        self.latch_commands: list[tuple[str, str]] = []
        self.maintenance_responses: list[str] = []
        self._chunks: list[bytes] = []
        self.writes: list[bytes] = []
        self.closed = False
        self.zeroed = False

    def write(self, data: bytes) -> int:
        self.writes.append(data)
        text = data.decode("ascii", errors="replace").strip()
        fields = text.split()
        if len(fields) == 3 and fields[0] == "maint":
            request_id, command = fields[1], fields[2]
            if command == "status" and self.defer_status:
                self.pending_status_id = request_id
                return len(data)
            if command == "home" and self.defer_home:
                self.pending_home_id = request_id
                return len(data)
            if command in ("manual_up", "manual_down") and self.defer_manual_start:
                self.pending_manual_start = (request_id, command)
                return len(data)
            if command in ("latch_hold", "latch_open", "latch_status"):
                self.latch_commands.append((request_id, command))
                if command != "latch_status" and self.defer_latch_commands:
                    self.pending_latch_command = (request_id, command)
                    return len(data)
            if request_id == "0" and command in (
                "manual_keepalive_up",
                "manual_keepalive_down",
            ):
                self._receive_manual_keepalive(command)
                return len(data)
            self._respond(request_id, command)
        return len(data)

    def release_status(self) -> None:
        request_id = self.pending_status_id
        self.pending_status_id = None
        self.defer_status = False
        if request_id is not None:
            self._respond(request_id, "status")

    def release_home(self) -> None:
        request_id = self.pending_home_id
        self.pending_home_id = None
        self.defer_home = False
        if request_id is not None:
            self._respond(request_id, "home")

    def release_manual_start(self) -> None:
        pending = self.pending_manual_start
        self.pending_manual_start = None
        self.defer_manual_start = False
        if pending is not None:
            self._respond(*pending)

    def release_latch_command(self) -> None:
        pending = self.pending_latch_command
        self.pending_latch_command = None
        self.defer_latch_commands = False
        if pending is not None:
            self._respond(*pending)

    def _receive_manual_keepalive(self, command: str) -> None:
        if self.drop_manual_keepalives:
            return
        direction = "up" if command.endswith("_up") else "down"
        accepted = self.manual_active and self.manual_direction == direction
        received_at = time.monotonic()
        self.manual_keepalive_received.append((direction, received_at, accepted))
        if accepted:
            self.last_manual_keepalive = received_at

    def _respond(self, request_id: str, command: str) -> None:
        if command == "status":
            active_mode = (
                f"manual_{self.manual_direction}" if self.manual_active else "idle"
            )
            response = (
                f"maintenance: id={request_id} type=status ok=1 homed={int(self.homed)} "
                f"upper_limit={int(self.upper_active)} lower_limit={int(self.lower_active)} "
                f"fault={self.fault_code} active_mode={active_mode} "
                f"travel_span_steps={1200 if self.homed else 0} position_steps=0 "
                "steps_per_mm=40.000\r\n"
            )
        elif command == "switches":
            response = (
                f"maintenance: id={request_id} type=switches ok=1 "
                f"upper={int(self.upper_active)} lower={int(self.lower_active)}\r\n"
            )
        elif command == "angle":
            if self.angle_available:
                response = (
                    f"maintenance: id={request_id} type=angle ok=1 raw=2048 "
                    f"degrees={0.0 if self.zeroed else 2.5:.2f}\r\n"
                )
            else:
                response = f"maintenance: id={request_id} type=angle ok=0 error=as5600_unavailable\r\n"
        elif command == "home":
            if self.latch_state != "open":
                response = (
                    f"maintenance: id={request_id} type=home ok=0 homed=0 "
                    f"fault={self.fault_code} error=latch_not_open\r\n"
                )
            else:
                self.home_motion_start_count += 1
                self.homed = self.home_succeeds
                response = (
                    f"maintenance: id={request_id} type=home ok={int(self.home_succeeds)} "
                    f"homed={int(self.homed)} fault={self.fault_code} "
                    f"span_steps={1200 if self.homed else 0} position_steps=0 steps_per_mm=40.000"
                    f"{' error=home_test_failure' if not self.home_succeeds else ''}\r\n"
                )
        elif command == "zero":
            self.zeroed = self.angle_available
            response = (
                f"maintenance: id={request_id} type=zero ok={int(self.zeroed)} "
                f"zeroed={int(self.zeroed)} raw=2048 degrees=0.00"
                f"{' error=as5600_unavailable' if not self.zeroed else ''}\r\n"
            )
        elif command == "fault_clear":
            previous_fault = self.fault_code
            error = None
            if self.manual_active:
                error = "motion_active"
            elif self.fault_code == 1 and self.upper_active:
                error = "upper_limit_still_active"
            elif self.fault_code == 2 and self.lower_active:
                error = "lower_limit_still_active"
            elif self.fault_code == 3 and not self.angle_available:
                error = "angle_sensor_unavailable"
            elif self.fault_code not in (0, 1, 2, 3):
                error = "unknown_fault_code"
            if error is not None:
                response = (
                    f"maintenance: id={request_id} type=fault_clear ok=0 "
                    f"fault={self.fault_code} error={error}\r\n"
                )
            else:
                if previous_fault != 0:
                    self.fault_code = 0
                    self._chunks.append(
                        f"event: type=fault_cleared previous_fault={previous_fault}\r\n".encode("ascii")
                    )
                response = (
                    f"maintenance: id={request_id} type=fault_clear ok=1 fault=0 "
                    f"cleared={int(previous_fault != 0)}\r\n"
                )
        elif command == "latch_status":
            response = (
                f"maintenance: id={request_id} type=latch_status ok=1 state={self.latch_state} "
                f"commanded={int(self.latch_state != 'unknown')}\r\n"
            )
        elif command == "fault_clear":
            previous_fault = self.fault_code
            error = None
            if self.manual_active:
                error = "motion_active"
            elif self.fault_code == 1 and self.upper_active:
                error = "upper_limit_still_active"
            elif self.fault_code == 2 and self.lower_active:
                error = "lower_limit_still_active"
            elif self.fault_code == 3 and not self.angle_available:
                error = "angle_sensor_unavailable"
            elif self.fault_code not in (0, 1, 2, 3):
                error = "unknown_fault_code"
            if error is not None:
                response = (
                    f"maintenance: id={request_id} type=fault_clear ok=0 "
                    f"fault={self.fault_code} error={error}\r\n"
                )
            else:
                if previous_fault != 0:
                    self.fault_code = 0
                    self._chunks.append(
                        f"event: type=fault_cleared previous_fault={previous_fault}\r\n".encode("ascii")
                    )
                response = (
                    f"maintenance: id={request_id} type=fault_clear ok=1 fault=0 "
                    f"cleared={int(previous_fault != 0)}\r\n"
                )
        elif command in ("latch_hold", "latch_open"):
            if self.reject_latch_commands:
                response = (
                    f"maintenance: id={request_id} type={command} ok=0 state={self.latch_state} "
                    "error=servo_test_rejected\r\n"
                )
            else:
                self.latch_state = "hold" if command == "latch_hold" else "open"
                response = (
                    f"maintenance: id={request_id} type={command} ok=1 state={self.latch_state} "
                    "commanded=1 pulse_us=1500\r\n"
                )
        elif command in ("manual_up", "manual_down"):
            direction = "up" if command == "manual_up" else "down"
            limit_active = self.upper_active if direction == "up" else self.lower_active
            if limit_active:
                limit_name = "upper" if direction == "up" else "lower"
                response = (
                    f"maintenance: id={request_id} type={command} ok=0 active=0 "
                    f"error={limit_name}_limit_active\r\n"
                )
            else:
                self.manual_active = True
                self.manual_direction = direction
                self.manual_started_at = time.monotonic()
                self.last_manual_keepalive = self.manual_started_at
                self._chunks.append(
                    f"event: type=manual_motion_started direction={self.manual_direction}\r\n".encode("ascii")
                )
                response = (
                    f"maintenance: id={request_id} type={command} ok=1 "
                    f"active=1 direction={self.manual_direction}\r\n"
                )
        elif command in ("manual_keepalive_up", "manual_keepalive_down"):
            if self.drop_manual_keepalives:
                return
            self._receive_manual_keepalive(command)
            response = f"maintenance: id={request_id} type={command} ok=1 active=1\r\n"
        elif command == "manual_stop":
            if self.drop_manual_stop:
                return
            direction = self.manual_direction or "unknown"
            self.manual_active = False
            self.manual_direction = None
            self._chunks.append(
                f"event: type=manual_motion_stopped direction={direction} reason=operator_release\r\n".encode("ascii")
            )
            response = (
                f"maintenance: id={request_id} type=manual_stop ok=1 active=0 "
                "position_steps=0\r\n"
            )
        else:
            response = f"maintenance: id={request_id} type=error ok=0 error=unsupported_command\r\n"
        if self.prepend_stale_response:
            self.prepend_stale_response = False
            stale_id = int(request_id) + 1000
            self._chunks.append(
                f"maintenance: id={stale_id} type={command} ok=0 error=stale_response\r\n".encode("ascii")
            )
        self.maintenance_responses.append(response.strip())
        self._chunks.append(response.encode("ascii"))

    def read(self, size: int) -> bytes:
        del size
        if self._chunks:
            return self._chunks.pop(0)
        if self.manual_active and (
            (self.manual_direction == "up" and self.upper_active)
            or (self.manual_direction == "down" and self.lower_active)
        ):
            direction = self.manual_direction or "unknown"
            limit = "upper_limit" if direction == "up" else "lower_limit"
            self.manual_active = False
            self.manual_direction = None
            self._chunks.append(
                f"event: type=manual_motion_stopped direction={direction} reason={limit}\r\n".encode("ascii")
            )
            return self._chunks.pop(0)
        if (
            self.manual_active
            and time.monotonic() - self.last_manual_keepalive >= self.manual_deadman_timeout_s
        ):
            direction = self.manual_direction or "unknown"
            self.manual_active = False
            self.manual_direction = None
            self._chunks.append(
                f"event: type=manual_motion_stopped direction={direction} reason=deadman_timeout\r\n".encode("ascii")
            )
            return self._chunks.pop(0)
        time.sleep(0.003)
        return b""

    def close(self) -> None:
        self.closed = True


class MaintenanceServiceTests(unittest.TestCase):
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

    def create_connected_controller(self, **serial_options):
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        serial_instances: list[MaintenanceFakeSerial] = []

        def serial_factory(port: str, baud_rate: int, **kwargs: object) -> MaintenanceFakeSerial:
            device = MaintenanceFakeSerial(port, baud_rate, **serial_options, **kwargs)
            serial_instances.append(device)
            return device

        serial_client = SerialTelemetryClient(
            capture_root=Path(temporary_directory.name),
            serial_factory=serial_factory,
            startup_delay_s=0.0,
        )
        controller = WorkbenchController(serial_client=serial_client)
        self.addCleanup(controller.shutdown)
        controller.select_data_source(DataSourceMode.REAL)
        controller.connect_real("COM_MAINTENANCE_FAKE")
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.connection == ConnectionState.CONNECTED)
        )
        self.assertTrue(serial_instances)
        if serial_instances[0].defer_status:
            self.assertTrue(self.wait_for(lambda: serial_instances[0].pending_status_id is not None))
            serial_instances[0].release_status()
            self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))
            serial_instances[0].defer_status = True
        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))
        controller._maintenance_fake_devices = serial_instances
        return controller, serial_instances[0]

    def test_manual_motion_signal_subscriptions_are_single(self) -> None:
        event_calls = []
        telemetry_calls = []
        original_event_handler = MaintenanceService._on_device_event
        original_position_handler = MaintenancePage._update_manual_position

        def observe_event_handler(service, event) -> None:
            event_calls.append(event)
            original_event_handler(service, event)

        def observe_position_handler(page, sample) -> None:
            telemetry_calls.append(sample)
            original_position_handler(page, sample)

        controller = WorkbenchController()
        self.addCleanup(controller.shutdown)
        with (
            patch.object(MaintenanceService, "_on_device_event", observe_event_handler),
            patch.object(MaintenancePage, "_update_manual_position", observe_position_handler),
        ):
            window = MainWindow(controller)
            self.addCleanup(window.close)
            controller.serial_client.device_event_received.emit(
                DeviceEvent(
                    "manual_motion_stopped",
                    {"direction": "up", "reason": "upper_limit"},
                    "event: type=manual_motion_stopped direction=up reason=upper_limit",
                )
            )
            controller.state.telemetry_received.emit(
                TelemetrySample(1, 0.02, 0.0, 1.0, source="real")
            )

        self.assertEqual(len(event_calls), 1)
        self.assertEqual(len(telemetry_calls), 1)

    def test_latch_starts_unknown_and_does_not_affect_system_check(self) -> None:
        controller, device = self.create_connected_controller()
        self.assertEqual(controller.maintenance.snapshot.latch_state, PendulumLatchState.UNKNOWN)
        self.assertEqual(device.latch_commands, [])

        self.run_system_check(controller)

        self.assertEqual(controller.maintenance.snapshot.bit_state, BitResultState.PASSED)
        self.assertEqual(controller.maintenance.snapshot.latch_state, PendulumLatchState.UNKNOWN)
        self.assertEqual(device.latch_commands, [])

    def test_latch_hold_and_open_require_matching_correlated_confirmation(self) -> None:
        controller, device = self.create_connected_controller(defer_latch_commands=True)

        controller.maintenance.command_latch(PendulumLatchState.HOLD)

        self.assertTrue(self.wait_for(lambda: device.pending_latch_command is not None))
        hold_request_id, hold_command = device.pending_latch_command
        self.assertGreater(int(hold_request_id), 0)
        self.assertEqual(hold_command, "latch_hold")
        self.assertEqual(controller.maintenance.snapshot.latch_state, PendulumLatchState.COMMANDING_HOLD)
        self.assertEqual(device.latch_state, "unknown")
        self.assertTrue(controller.firmware_commands.is_busy)

        device.release_latch_command()

        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.HOLD
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(device.latch_state, "hold")

        device.defer_latch_commands = True
        controller.maintenance.command_latch(PendulumLatchState.OPEN)
        self.assertTrue(self.wait_for(lambda: device.pending_latch_command is not None))
        open_request_id, open_command = device.pending_latch_command
        self.assertGreater(int(open_request_id), 0)
        self.assertEqual(open_command, "latch_open")
        self.assertEqual(controller.maintenance.snapshot.latch_state, PendulumLatchState.COMMANDING_OPEN)
        self.assertEqual(device.latch_state, "hold")

        device.release_latch_command()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.OPEN
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(device.latch_state, "open")

    def test_latch_status_reads_commanded_state_without_moving_servo(self) -> None:
        controller, device = self.create_connected_controller()
        device.latch_state = "open"

        controller.maintenance.request_latch_status()

        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.OPEN
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(len(device.latch_commands), 1)
        self.assertTrue(all(command == "latch_status" for _, command in device.latch_commands))

    def test_latch_ui_routes_hold_and_open_without_requiring_bit(self) -> None:
        controller, device = self.create_connected_controller(defer_latch_commands=True)
        window = MainWindow(controller)
        page = window.maintenance_page

        self.assertEqual(controller.maintenance.snapshot.latch_state, PendulumLatchState.UNKNOWN)
        self.assertEqual(controller.maintenance.snapshot.bit_state, BitResultState.NOT_RUN)
        self.assertTrue(page.latch_hold_button.isEnabled())
        self.assertTrue(page.latch_open_button.isEnabled())
        self.assertIn("does not set the pendulum initial angle", page.latch_commissioning_note.text())

        page.latch_hold_button.click()
        self.assertTrue(self.wait_for(lambda: device.pending_latch_command is not None))
        self.assertEqual(controller.maintenance.snapshot.latch_state, PendulumLatchState.COMMANDING_HOLD)
        self.assertEqual(device.latch_state, "unknown")
        device.release_latch_command()
        self.assertTrue(
            self.wait_for(lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.HOLD)
        )
        self.assertEqual(page.latch_state_value.text(), PendulumLatchState.HOLD.value)

        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))
        device.defer_latch_commands = True
        page.latch_open_button.click()
        self.assertTrue(self.wait_for(lambda: device.pending_latch_command is not None))
        self.assertEqual(controller.maintenance.snapshot.latch_state, PendulumLatchState.COMMANDING_OPEN)
        self.assertEqual(device.latch_state, "hold")
        device.release_latch_command()
        self.assertTrue(
            self.wait_for(lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.OPEN)
        )
        window.close()

    def test_latch_rejection_and_timeout_are_unconfirmed(self) -> None:
        controller, device = self.create_connected_controller(reject_latch_commands=True)

        controller.maintenance.command_latch(PendulumLatchState.OPEN)

        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.UNCONFIRMED
            )
        )
        self.assertIn("servo test rejected", controller.maintenance.snapshot.latch_message)

        controller, device = self.create_connected_controller(defer_latch_commands=True)
        with patch.object(FirmwareCommandService, "_RESPONSE_TIMEOUT_MS", 25):
            controller.maintenance.command_latch(PendulumLatchState.HOLD)
            self.assertTrue(
                self.wait_for(
                    lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.UNCONFIRMED,
                    timeout_s=1.0,
                )
            )
        self.assertEqual(device.latch_state, "unknown")

    def test_latch_interlocks_cover_homing_manual_motion_and_recording(self) -> None:
        controller, device = self.create_connected_controller(defer_home=True)
        self.command_latch_open(controller)
        controller.maintenance.start_homing()
        self.assertTrue(self.wait_for(lambda: device.pending_home_id is not None))
        with self.assertRaisesRegex(RuntimeError, "maintenance operation"):
            controller.maintenance.command_latch(PendulumLatchState.HOLD)
        device.release_home()
        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))

        controller, device = self.create_connected_controller(initially_homed=True)
        self.run_system_check(controller)
        controller.maintenance.start_manual_motion("up")
        self.assertTrue(self.wait_for(lambda: device.manual_active))
        with self.assertRaisesRegex(RuntimeError, "maintenance operation"):
            controller.maintenance.command_latch(PendulumLatchState.OPEN)
        heartbeat_count = len(device.manual_keepalive_received)
        self.assertTrue(
            self.wait_for(lambda: len(device.manual_keepalive_received) >= heartbeat_count + 3)
        )
        self.assertEqual(device.latch_commands, [])
        controller.maintenance.stop_manual_motion()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.manual_motion == ManualMotionState.IDLE
            )
        )
        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))

        controller.state.set_recording(RecordingState.RECORDING)
        with self.assertRaisesRegex(RuntimeError, "recording"):
            controller.maintenance.command_latch(PendulumLatchState.HOLD)
        self.assertEqual(device.latch_commands, [])

    def run_system_check(self, controller: WorkbenchController) -> None:
        controller.maintenance.run_system_check()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.bit_state
                in (BitResultState.PASSED, BitResultState.FAILED)
            )
        )

    def command_latch_open(self, controller: WorkbenchController) -> None:
        controller.maintenance.command_latch(PendulumLatchState.OPEN)
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.OPEN
                and not controller.firmware_commands.is_busy
            )
        )

    def test_disconnected_controls_and_system_check_are_interlocked(self) -> None:
        controller = WorkbenchController()
        self.addCleanup(controller.shutdown)
        window = MainWindow(controller)
        page = window.maintenance_page

        self.assertFalse(page.run_system_check_button.isEnabled())
        self.assertFalse(page.home_button.isEnabled())
        self.assertFalse(page.zero_angle_button.isEnabled())
        self.assertFalse(page.manual_up_button.isEnabled())
        self.assertFalse(page.manual_down_button.isEnabled())
        self.assertEqual(page.readiness_indicator.text(), ReadinessState.NOT_CONNECTED.value)
        with self.assertRaisesRegex(RuntimeError, "Connect to the STM32"):
            controller.maintenance.run_system_check()
        window.close()

    def test_bit_pass_updates_each_check_and_requires_home(self) -> None:
        controller, device = self.create_connected_controller(prepend_stale_response=True)
        events = []
        controller.state.event_added.connect(events.append)

        self.run_system_check(controller)

        snapshot = controller.maintenance.snapshot
        self.assertEqual(snapshot.bit_state, BitResultState.PASSED)
        self.assertEqual(snapshot.readiness, ReadinessState.BIT_PASSED_HOME_REQUIRED)
        self.assertIsNotNone(snapshot.latest_check_at)
        self.assertTrue(all(result.state == CheckState.PASS for result in snapshot.checks))
        self.assertIn("electrical actuation not tested", snapshot.checks[1].detail)
        self.assertEqual(controller.state.snapshot.hardware_status.homed, False)
        self.assertEqual(controller.state.snapshot.hardware_status.fault_known, True)
        self.assertTrue(any("System check passed" in event.message for event in events))
        self.assertTrue(any(write.startswith(b"maint ") for write in device.writes))
        self.assertFalse(any(write == b"home\r\n" for write in device.writes))

    def test_navigation_during_bit_and_homing_keeps_the_same_session(self) -> None:
        controller, device = self.create_connected_controller(
            defer_status=True,
            defer_home=True,
        )
        window = MainWindow(controller)
        serial_client = controller.serial_client
        controller.maintenance.run_system_check()
        self.assertTrue(self.wait_for(lambda: device.pending_status_id is not None))

        window.experiment_navigation_button.click()
        self.assertIs(window.page_stack.currentWidget(), window.experiment_page)
        self.assertEqual(controller.maintenance.snapshot.operation, "system_check")
        self.assertIs(controller.serial_client, serial_client)
        device.release_status()
        self.assertTrue(
            self.wait_for(lambda: controller.maintenance.snapshot.bit_state == BitResultState.PASSED)
        )

        self.command_latch_open(controller)
        controller.maintenance.start_homing()
        self.assertTrue(self.wait_for(lambda: device.pending_home_id is not None))
        window.live_navigation_button.click()
        self.assertEqual(controller.state.snapshot.home_status, HomeStatus.HOMING)
        self.assertIs(controller.serial_client, serial_client)
        controller.disconnect_real()
        self.assertEqual(controller.state.snapshot.connection, ConnectionState.CONNECTED)
        self.assertFalse(window.live_page.disconnect_button.isEnabled())
        status_poll_count = device.writes.count(b"status\r\n")
        deadline = time.monotonic() + 1.1
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            time.sleep(0.005)
        self.assertEqual(device.writes.count(b"status\r\n"), status_poll_count)
        device.release_home()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.home_status == HomeStatus.HOMED)
        )
        window.close()

    def test_bit_pass_with_firmware_reported_homed_state_is_ready(self) -> None:
        controller, _device = self.create_connected_controller(initially_homed=True)

        self.run_system_check(controller)

        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.SYSTEM_READY)

    def test_bit_fault_fails_and_is_logged(self) -> None:
        controller, device = self.create_connected_controller(fault_code=3)
        events = []
        controller.state.event_added.connect(events.append)

        self.run_system_check(controller)

        snapshot = controller.maintenance.snapshot
        self.assertEqual(snapshot.bit_state, BitResultState.FAILED)
        self.assertEqual(snapshot.readiness, ReadinessState.FAULT)
        self.assertEqual(snapshot.checks[-1].state, CheckState.FAIL)
        self.assertTrue(any(event.severity.value == "Error" for event in events))
        self.assertNotIn("fault_clear", [line.split("type=")[-1].split()[0] for line in device.maintenance_responses])

    def test_fault_clear_ui_command_refreshes_status_and_reenables_home_after_check(self) -> None:
        controller, device = self.create_connected_controller(fault_code=3, initially_homed=True)
        window = MainWindow(controller)
        self.addCleanup(window.close)
        page = window.maintenance_page
        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))

        self.assertEqual(controller.state.snapshot.hardware_status.fault, "Firmware fault 3")
        self.assertTrue(page.clear_fault_button.isEnabled())
        self.assertFalse(page.home_button.isEnabled())
        with self.assertRaisesRegex(RuntimeError, "blocked by the firmware fault"):
            controller.maintenance.start_homing()
        home_count = device.home_motion_start_count
        zeroed = device.zeroed

        page.clear_fault_button.click()

        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.bit_state == BitResultState.PASSED
                and controller.maintenance.snapshot.readiness == ReadinessState.SYSTEM_READY
                and controller.state.snapshot.hardware_status.fault_known
                and controller.state.snapshot.hardware_status.fault is None
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertTrue(any(b"fault_clear" in write for write in device.writes))
        self.assertTrue(any("type=status" in response for response in device.maintenance_responses))
        self.assertEqual(page.check_rows[CheckName.FAULT_STATE][0].text(), CheckState.PASS.value)
        self.assertIn("Fault cleared", page.clear_fault_result_value.text())
        self.assertTrue(page.clear_fault_button.isEnabled() is False)
        self.assertEqual(device.home_motion_start_count, home_count)
        self.assertEqual(device.zeroed, zeroed)

        page.latch_state_value.setText(PendulumLatchState.OPEN.value)
        device.latch_state = "open"
        controller.maintenance.request_latch_status()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.OPEN
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertTrue(page.home_button.isEnabled())

    def test_fault_clear_ui_shows_condition_specific_rejection(self) -> None:
        controller, device = self.create_connected_controller(
            fault_code=3,
            angle_available=False,
        )
        window = MainWindow(controller)
        self.addCleanup(window.close)
        page = window.maintenance_page
        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))
        self.assertTrue(page.clear_fault_button.isEnabled())

        page.clear_fault_button.click()

        self.assertTrue(
            self.wait_for(
                lambda: "Cannot clear fault: angle sensor unavailable"
                in page.clear_fault_result_value.text()
            )
        )
        self.assertEqual(device.fault_code, 3)
        self.assertTrue(controller.state.snapshot.hardware_status.fault)

    def test_fault_clear_ui_is_disabled_when_no_firmware_fault_is_reported(self) -> None:
        controller, _device = self.create_connected_controller(fault_code=0)
        window = MainWindow(controller)
        self.addCleanup(window.close)
        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))

        self.assertFalse(window.maintenance_page.clear_fault_button.isEnabled())

    def test_fault_clear_rejects_faults_whose_condition_is_still_active(self) -> None:
        blocked_cases = (
            (1, True, False, "upper_limit_still_active"),
            (2, False, True, "lower_limit_still_active"),
            (3, False, False, "angle_sensor_unavailable"),
        )
        for fault_code, upper_active, lower_active, expected_error in blocked_cases:
            with self.subTest(fault_code=fault_code):
                controller, device = self.create_connected_controller(
                    fault_code=fault_code,
                    upper_active=upper_active,
                    lower_active=lower_active,
                    angle_available=fault_code != 3,
                )
                controller.maintenance.clear_fault()
                self.assertTrue(
                    self.wait_for(
                        lambda: not controller.firmware_commands.is_busy
                        and controller.maintenance.snapshot.operation is None
                    )
                )
                response = FirmwareProtocolParser.parse_maintenance_response(
                    device.maintenance_responses[-1]
                )
                self.assertEqual(response.response_type, "fault_clear")
                self.assertFalse(response.ok)
                self.assertEqual(response.fields["error"], expected_error)
                self.assertEqual(device.fault_code, fault_code)
                self.assertFalse(any("fault_cleared" in write.decode("ascii", errors="ignore")
                                     for write in device.writes))

    def test_fault_clear_is_correlated_safe_and_refreshes_system_check(self) -> None:
        controller, device = self.create_connected_controller(
            fault_code=1,
            initially_homed=True,
        )
        device.latch_state = "open"
        controller.maintenance.request_latch_status()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.OPEN
                and not controller.firmware_commands.is_busy
            )
        )
        with self.assertRaisesRegex(RuntimeError, "blocked by the firmware fault"):
            controller.maintenance.start_homing()

        home_count = device.home_motion_start_count
        angle_zero = device.zeroed
        latch_state = device.latch_state
        events = []
        controller.state.event_added.connect(events.append)
        controller.maintenance.clear_fault()

        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.bit_state == BitResultState.PASSED
                and controller.maintenance.snapshot.readiness == ReadinessState.SYSTEM_READY
                and controller.state.snapshot.hardware_status.fault_known
                and controller.state.snapshot.hardware_status.fault is None
                and not controller.firmware_commands.is_busy
            )
        )
        response = FirmwareProtocolParser.parse_maintenance_response(
            next(
                line for line in device.maintenance_responses
                if "type=fault_clear" in line
            )
        )
        self.assertTrue(response.ok)
        self.assertEqual(response.fields["cleared"], "1")
        self.assertEqual(response.fields["fault"], "0")
        self.assertEqual(device.fault_code, 0)
        self.assertEqual(device.home_motion_start_count, home_count)
        self.assertEqual(device.zeroed, angle_zero)
        self.assertEqual(device.latch_state, latch_state)
        self.assertEqual(controller.state.snapshot.hardware_status.homed, True)
        self.assertEqual(controller.state.snapshot.hardware_status.travel_span_steps, 1200)
        self.assertEqual(controller.state.snapshot.hardware_status.position_steps, 0)
        self.assertTrue(any("System check passed" in event.message for event in events))

        controller.maintenance.start_homing()
        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.home_status == HomeStatus.HOMED
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(device.home_motion_start_count, home_count + 1)

    def test_fault_clear_succeeds_after_lower_limit_or_sensor_condition_recovers(self) -> None:
        for fault_code in (2, 3):
            with self.subTest(fault_code=fault_code):
                controller, device = self.create_connected_controller(
                    fault_code=fault_code,
                    initially_homed=True,
                    angle_available=True,
                )
                controller.maintenance.clear_fault()
                self.assertTrue(
                    self.wait_for(
                        lambda: controller.maintenance.snapshot.bit_state == BitResultState.PASSED
                        and controller.maintenance.snapshot.readiness == ReadinessState.SYSTEM_READY
                        and not controller.firmware_commands.is_busy
                    )
                )
                response = FirmwareProtocolParser.parse_maintenance_response(
                    next(
                        line for line in device.maintenance_responses
                        if "type=fault_clear" in line
                    )
                )
                self.assertTrue(response.ok)
                self.assertEqual(response.fields["cleared"], "1")
                self.assertEqual(device.fault_code, 0)

    def test_fault_clear_with_no_active_fault_is_benign_and_runs_status_check(self) -> None:
        controller, device = self.create_connected_controller(initially_homed=True)
        events = []
        controller.state.event_added.connect(events.append)

        controller.maintenance.clear_fault()

        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.bit_state == BitResultState.PASSED
                and not controller.firmware_commands.is_busy
            )
        )
        response = FirmwareProtocolParser.parse_maintenance_response(
            next(line for line in device.maintenance_responses if "type=fault_clear" in line)
        )
        self.assertTrue(response.ok)
        self.assertEqual(response.fields["cleared"], "0")
        self.assertEqual(device.fault_code, 0)
        self.assertFalse(any("fault_cleared" in event.message for event in events))

    def test_fault_clear_is_blocked_while_motion_or_recording_is_active(self) -> None:
        controller, device = self.create_connected_controller(fault_code=3)
        for motion in (MotionState.RUNNING, MotionState.HOMING, MotionState.STOPPING):
            with self.subTest(motion=motion):
                controller.state.set_motion(motion)
                with self.assertRaisesRegex(RuntimeError, "motion is active"):
                    controller.maintenance.clear_fault()
                self.assertFalse(any(b"fault_clear" in write for write in device.writes))

        controller.state.set_motion(MotionState.FAULT)
        controller.state.set_recording(RecordingState.RECORDING)
        with self.assertRaisesRegex(RuntimeError, "recording is active"):
            controller.maintenance.clear_fault()
        self.assertFalse(any(b"fault_clear" in write for write in device.writes))

    def test_bit_as5600_failure_is_visible_and_logged(self) -> None:
        controller, device = self.create_connected_controller(angle_available=False)
        events = []
        controller.state.event_added.connect(events.append)
        window = MainWindow(controller)

        window.maintenance_page.run_system_check_button.click()
        self.assertTrue(
            self.wait_for(lambda: controller.maintenance.snapshot.bit_state == BitResultState.FAILED)
        )

        snapshot = controller.maintenance.snapshot
        self.assertEqual(snapshot.bit_state, BitResultState.FAILED)
        self.assertEqual(snapshot.readiness, ReadinessState.FAULT)
        self.assertEqual(snapshot.checks[3].state, CheckState.FAIL)
        state_label, detail_label = window.maintenance_page.check_rows[CheckName.ANGLE_SENSOR]
        self.assertEqual(state_label.text(), CheckState.FAIL.value)
        self.assertIn("AS5600", detail_label.text())
        self.assertTrue(any("system check failed" in event.message.lower() for event in events))

        device.angle_available = True
        window.maintenance_page.run_system_check_button.click()
        self.assertTrue(
            self.wait_for(lambda: controller.maintenance.snapshot.bit_state == BitResultState.PASSED)
        )
        self.assertEqual(
            controller.maintenance.snapshot.readiness,
            ReadinessState.BIT_PASSED_HOME_REQUIRED,
        )
        window.close()

    def test_runtime_as5600_loss_invalidates_bit_until_a_new_check_passes(self) -> None:
        controller, device = self.create_connected_controller(initially_homed=True)
        self.run_system_check(controller)
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.SYSTEM_READY)

        controller.serial_client.device_message_received.emit("angle sensor unavailable")
        self.assertEqual(controller.maintenance.snapshot.bit_state, BitResultState.FAILED)
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.FAULT)
        self.assertEqual(controller.maintenance.snapshot.checks[3].state, CheckState.FAIL)

        controller.serial_client.device_message_received.emit("angle sensor communication restored")
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.CHECK_REQUIRED)
        self.assertFalse(controller.maintenance.snapshot.checks[3].state == CheckState.PASS)

        device.angle_available = True
        self.run_system_check(controller)
        self.assertEqual(controller.maintenance.snapshot.bit_state, BitResultState.PASSED)
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.SYSTEM_READY)

    def test_disconnect_invalidates_bit_result_and_readiness(self) -> None:
        controller, _device = self.create_connected_controller()
        self.run_system_check(controller)
        self.assertEqual(controller.maintenance.snapshot.bit_state, BitResultState.PASSED)

        controller.disconnect_real()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.connection == ConnectionState.DISCONNECTED)
        )

        self.assertEqual(controller.maintenance.snapshot.bit_state, BitResultState.NOT_RUN)
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.NOT_CONNECTED)
        controller.connect_real("COM_MAINTENANCE_FAKE")
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.connection == ConnectionState.CONNECTED)
        )
        self.assertEqual(controller.maintenance.snapshot.bit_state, BitResultState.NOT_RUN)
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.CHECK_REQUIRED)

    def test_disconnect_during_bit_marks_result_incomplete(self) -> None:
        controller, _device = self.create_connected_controller(defer_status=True)
        controller.maintenance.run_system_check()
        self.assertTrue(
            self.wait_for(lambda: controller.maintenance.commands.is_busy)
        )

        controller.disconnect_real()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.connection == ConnectionState.DISCONNECTED)
        )

        self.assertEqual(controller.maintenance.snapshot.bit_state, BitResultState.INCOMPLETE)
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.NOT_CONNECTED)
        self.assertTrue(
            any(result.state == CheckState.WARNING for result in controller.maintenance.snapshot.checks)
        )

    def test_missing_correlated_response_times_out_and_fails_bit(self) -> None:
        controller, _device = self.create_connected_controller(defer_status=True)
        controller.firmware_commands._RESPONSE_TIMEOUT_MS = 50
        events = []
        controller.state.event_added.connect(events.append)

        controller.maintenance.run_system_check()
        self.assertTrue(
            self.wait_for(lambda: controller.maintenance.snapshot.bit_state == BitResultState.FAILED)
        )

        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.FAULT)
        self.assertTrue(
            any("Timed out waiting for maintenance command response" in event.message for event in events)
        )

    def test_home_is_blocked_by_reported_firmware_fault(self) -> None:
        controller, _device = self.create_connected_controller(fault_code=2)
        self.run_system_check(controller)

        with self.assertRaisesRegex(RuntimeError, "blocked by the firmware fault"):
            controller.maintenance.start_homing()
        self.assertIsNone(controller.maintenance.snapshot.operation)

    def test_home_requires_latch_open_and_firmware_rejects_unknown_or_hold(self) -> None:
        controller, device = self.create_connected_controller()

        for latch_state in ("unknown", "hold"):
            device.latch_state = latch_state
            controller.firmware_commands.home()
            self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))
            response = FirmwareProtocolParser.parse_maintenance_response(
                device.maintenance_responses[-1]
            )
            self.assertEqual(response.response_type, "home")
            self.assertFalse(response.ok)
            self.assertEqual(response.fields.get("error"), "latch_not_open")
            self.assertEqual(device.home_motion_start_count, 0)

        self.command_latch_open(controller)
        controller.maintenance.start_homing()
        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.home_status == HomeStatus.HOMED
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(device.home_motion_start_count, 1)
        self.assertEqual([command for _, command in device.latch_commands], ["latch_open"])

    def test_home_button_tracks_confirmed_latch_state_and_session(self) -> None:
        controller, device = self.create_connected_controller(defer_latch_commands=True)
        window = MainWindow(controller)
        page = window.maintenance_page

        self.assertEqual(page.maintenance.snapshot.latch_state, PendulumLatchState.UNKNOWN)
        self.assertEqual(device.latch_commands, [])
        self.assertFalse(page.home_button.isEnabled())
        self.assertFalse(page.home_latch_interlock_note.isHidden())
        self.assertEqual(page.home_latch_interlock_note.text(), "Homing requires Pendulum Latch OPEN.")

        controller.maintenance.command_latch(PendulumLatchState.HOLD)
        self.assertTrue(self.wait_for(lambda: device.pending_latch_command is not None))
        self.assertEqual(
            controller.maintenance.snapshot.latch_state,
            PendulumLatchState.COMMANDING_HOLD,
        )
        self.assertFalse(page.home_button.isEnabled())
        self.assertFalse(page.home_latch_interlock_note.isHidden())
        device.release_latch_command()
        self.assertTrue(
            self.wait_for(lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.HOLD)
        )
        self.assertFalse(page.home_button.isEnabled())

        device.defer_latch_commands = True
        controller.maintenance.command_latch(PendulumLatchState.OPEN)
        self.assertTrue(self.wait_for(lambda: device.pending_latch_command is not None))
        self.assertEqual(
            controller.maintenance.snapshot.latch_state,
            PendulumLatchState.COMMANDING_OPEN,
        )
        self.assertFalse(page.home_button.isEnabled())
        self.assertFalse(page.home_latch_interlock_note.isHidden())
        device.release_latch_command()
        self.assertTrue(
            self.wait_for(lambda: controller.maintenance.snapshot.latch_state == PendulumLatchState.OPEN)
        )
        self.assertTrue(page.home_button.isEnabled())
        self.assertTrue(page.home_latch_interlock_note.isHidden())

        controller.disconnect_real()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.connection == ConnectionState.DISCONNECTED)
        )
        self.assertEqual(page.maintenance.snapshot.latch_state, PendulumLatchState.UNKNOWN)
        self.assertFalse(page.home_button.isEnabled())

        controller.connect_real("COM_MAINTENANCE_FAKE")
        self.assertTrue(
            self.wait_for(
                lambda: controller.state.snapshot.connection == ConnectionState.CONNECTED
                and controller.state.snapshot.hardware_status.active_motion_mode == "Idle"
                and not controller.firmware_commands.is_busy
            )
        )
        self.assertEqual(page.maintenance.snapshot.latch_state, PendulumLatchState.UNKNOWN)
        self.assertFalse(page.home_button.isEnabled())
        reconnected_device = controller._maintenance_fake_devices[-1]
        self.assertIsNot(reconnected_device, device)
        self.assertEqual(reconnected_device.latch_commands, [])
        window.close()

    def test_home_and_zero_are_blocked_during_reported_motion(self) -> None:
        controller, _device = self.create_connected_controller()
        self.run_system_check(controller)
        window = MainWindow(controller)
        controller.state.set_hardware_status(
            HardwareStatus(active_motion_mode="Sine", homed=True, fault_known=True)
        )

        self.assertFalse(window.maintenance_page.home_button.isEnabled())
        self.assertFalse(window.maintenance_page.zero_angle_button.isEnabled())
        self.assertFalse(window.maintenance_page.manual_up_button.isEnabled())
        self.assertFalse(window.maintenance_page.manual_down_button.isEnabled())
        with self.assertRaisesRegex(RuntimeError, "Stop active device motion"):
            controller.maintenance.start_homing()
        with self.assertRaisesRegex(RuntimeError, "unavailable while device motion is active"):
            controller.maintenance.set_angle_zero()
        window.close()

    def test_home_requires_firmware_confirmation_and_updates_readiness(self) -> None:
        controller, device = self.create_connected_controller()
        self.run_system_check(controller)
        self.command_latch_open(controller)
        home_results = []
        controller.maintenance.snapshot_changed.connect(home_results.append)

        controller.maintenance.start_homing()
        self.assertEqual(controller.state.snapshot.home_status, HomeStatus.HOMING)
        self.assertEqual(controller.state.snapshot.motion, MotionState.HOMING)
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.operation is None
                and controller.state.snapshot.home_status == HomeStatus.HOMED
            )
        )

        snapshot = controller.maintenance.snapshot
        self.assertEqual(snapshot.readiness, ReadinessState.SYSTEM_READY)
        self.assertEqual(snapshot.travel_span_steps, 1200)
        self.assertEqual(snapshot.position_mm, 0.0)
        self.assertTrue(any(item.operation_message.startswith("Homing in progress") for item in home_results))
        self.assertTrue(any(write.endswith(b" home\r\n") for write in device.writes))
        self.assertTrue(any(write.endswith(b" status\r\n") for write in device.writes))

    def test_home_failure_is_reported_as_fault_without_homing(self) -> None:
        controller, _device = self.create_connected_controller(home_succeeds=False)
        self.run_system_check(controller)
        self.command_latch_open(controller)
        controller.maintenance.start_homing()

        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.operation is None
                and controller.state.snapshot.home_status == HomeStatus.HOMING_FAILED
            )
        )
        self.assertEqual(controller.state.snapshot.motion.value, "Fault")
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.FAULT)
        self.assertIn("home test failure", controller.maintenance.snapshot.operation_message)

        self.run_system_check(controller)
        self.assertEqual(controller.maintenance.snapshot.bit_state, BitResultState.PASSED)
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.FAULT)

    def test_zero_requires_ui_confirmation_and_firmware_angle_readback(self) -> None:
        controller, device = self.create_connected_controller()
        window = MainWindow(controller)
        page = window.maintenance_page

        with patch(
            "pendulum_workbench.ui.pages.maintenance_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.No,
        ):
            page.zero_angle_button.click()
        self.assertFalse(any(write.endswith(b" zero\r\n") for write in device.writes))

        with patch(
            "pendulum_workbench.ui.pages.maintenance_page.QMessageBox.question",
            return_value=QMessageBox.StandardButton.Yes,
        ):
            page.zero_angle_button.click()
        self.assertTrue(self.wait_for(lambda: controller.maintenance.snapshot.operation is None))
        self.assertEqual(controller.maintenance.snapshot.current_angle_deg, 0.0)
        self.assertTrue(any(write.endswith(b" zero\r\n") for write in device.writes))
        self.assertTrue(any(write.endswith(b" angle\r\n") for write in device.writes))
        window.close()

    def test_manual_up_and_down_are_press_hold_and_stop_on_release(self) -> None:
        controller, device = self.create_connected_controller(initially_homed=True)
        window = MainWindow(controller)
        window.show()
        self.qt_app.processEvents()
        page = window.maintenance_page
        self.assertFalse(page.manual_up_button.isEnabled())
        self.assertFalse(page.manual_down_button.isEnabled())
        self.run_system_check(controller)
        self.qt_app.processEvents()
        self.assertTrue(page.manual_up_button.isEnabled())
        self.assertTrue(page.manual_down_button.isEnabled())
        events = []
        controller.state.event_added.connect(events.append)

        QTest.mousePress(page.manual_up_button, Qt.MouseButton.LeftButton)
        self.assertTrue(
            self.wait_for(lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_UP)
        )
        self.assertTrue(device.manual_active)
        self.assertEqual(device.manual_direction, "up")
        self.assertFalse(page.manual_down_button.isEnabled())
        with self.assertRaisesRegex(RuntimeError, "Another maintenance operation or manual direction is active"):
            controller.maintenance.start_manual_motion("down")
        self.assertTrue(any(write.endswith(b" manual_up\r\n") for write in device.writes))
        self.assertTrue(self.wait_for(lambda: len(device.manual_keepalive_received) >= 5))
        up_times = [
            stamp for direction, stamp, accepted in device.manual_keepalive_received
            if direction == "up" and accepted
        ]
        self.assertGreaterEqual(up_times[-1] - up_times[0], 0.3)
        self.assertGreater(up_times[-1] - device.manual_started_at, 0.35)
        self.assertTrue(device.manual_active)
        self.assertTrue(
            all(0.07 <= second - first <= 0.18 for first, second in zip(up_times, up_times[1:]))
        )
        self.assertFalse(controller.firmware_commands.is_busy)
        self.qt_app.processEvents()
        self.assertFalse(any("manual_keepalive" in event.message for event in events))

        controller.state.publish_sample(TelemetrySample(201, 4.02, 1.0, 6.25, source="real"))
        self.assertIn("6.25 mm", page.manual_position_value.text())
        up_heartbeat_count = len(device.manual_keepalive_received)
        QTest.mouseRelease(page.manual_up_button, Qt.MouseButton.LeftButton)
        self.assertTrue(
            self.wait_for(lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.IDLE)
        )
        self.assertFalse(device.manual_active)
        self.assertTrue(any(write.endswith(b" manual_stop\r\n") for write in device.writes))
        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))
        self.qt_app.processEvents()
        self.assertEqual(len(device.manual_keepalive_received), up_heartbeat_count)

        QTest.mousePress(page.manual_down_button, Qt.MouseButton.LeftButton)
        self.assertTrue(
            self.wait_for(lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_DOWN)
        )
        self.assertEqual(device.manual_direction, "down")
        down_started_at = device.manual_started_at
        self.assertTrue(
            self.wait_for(lambda: len(device.manual_keepalive_received) >= up_heartbeat_count + 5)
        )
        down_times = [
            stamp for direction, stamp, accepted in device.manual_keepalive_received
            if direction == "down" and accepted
        ]
        self.assertGreaterEqual(down_times[-1] - down_times[0], 0.3)
        self.assertGreater(down_times[-1] - down_started_at, 0.35)
        self.assertTrue(device.manual_active)
        self.assertTrue(
            all(0.07 <= second - first <= 0.18 for first, second in zip(down_times, down_times[1:]))
        )
        down_heartbeat_count = len(device.manual_keepalive_received)
        QTest.mouseRelease(page.manual_down_button, Qt.MouseButton.LeftButton)
        self.assertTrue(
            self.wait_for(lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.IDLE)
        )
        self.assertFalse(device.manual_active)
        self.qt_app.processEvents()
        self.assertEqual(len(device.manual_keepalive_received), down_heartbeat_count)
        window.close()

    def test_manual_start_remains_correlated_and_waits_for_acknowledgement(self) -> None:
        controller, device = self.create_connected_controller(
            initially_homed=True,
            defer_manual_start=True,
        )
        self.run_system_check(controller)

        controller.maintenance.start_manual_motion("up")

        self.assertTrue(self.wait_for(lambda: device.pending_manual_start is not None))
        self.assertGreater(int(device.pending_manual_start[0]), 0)
        self.assertEqual(controller.maintenance.snapshot.manual_motion, ManualMotionState.STARTING_UP)
        self.assertTrue(controller.firmware_commands.is_busy)
        self.assertEqual(device.manual_keepalive_received, [])

        device.release_manual_start()

        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_UP
                and not controller.firmware_commands.is_busy
            )
        )
        controller.maintenance.stop_manual_motion()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.manual_motion == ManualMotionState.IDLE
            )
        )

    def test_disconnect_request_stops_manual_before_closing_serial(self) -> None:
        controller, device = self.create_connected_controller(initially_homed=True)
        self.run_system_check(controller)
        controller.maintenance.start_manual_motion("up")
        self.assertTrue(self.wait_for(lambda: device.manual_active))

        controller.disconnect_real()

        self.assertTrue(
            self.wait_for(lambda: not device.manual_active)
        )
        self.assertEqual(controller.state.snapshot.connection, ConnectionState.CONNECTED)
        self.assertTrue(any(write.endswith(b" manual_stop\r\n") for write in device.writes))
        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))
        controller.disconnect_real()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.connection == ConnectionState.DISCONNECTED)
        )

    def test_missing_manual_keepalive_expires_fake_firmware_deadman(self) -> None:
        controller, device = self.create_connected_controller(
            initially_homed=True,
            drop_manual_keepalives=True,
            manual_deadman_timeout_s=0.25,
        )
        self.run_system_check(controller)
        window = MainWindow(controller)
        page = window.maintenance_page
        window.show()
        self.qt_app.processEvents()
        page.manual_up_button.pressed.emit()

        self.assertTrue(
            self.wait_for(
                lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_UP
            )
        )
        self.assertTrue(
            self.wait_for(
                lambda: not device.manual_active
                and page.maintenance.snapshot.manual_motion == ManualMotionState.DEADMAN_STOP,
                timeout_s=1.0,
            )
        )
        self.assertFalse(device.manual_active)
        self.assertIn("deadman timeout", page.maintenance.snapshot.manual_motion_message)
        window.close()

    def test_wrong_direction_heartbeat_does_not_sustain_active_motion(self) -> None:
        controller, device = self.create_connected_controller(initially_homed=True)
        self.run_system_check(controller)
        controller.maintenance.start_manual_motion("up")
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_UP
            )
        )

        controller.maintenance._manual_keepalive.stop()
        controller.serial_client.set_manual_heartbeat(None)
        deadline = time.monotonic() + 0.7
        next_heartbeat = time.monotonic()
        while time.monotonic() < deadline and device.manual_active:
            if time.monotonic() >= next_heartbeat:
                controller.serial_client.set_manual_heartbeat("down")
                next_heartbeat += 0.1
            self.qt_app.processEvents()
            time.sleep(0.003)

        self.assertTrue(
            self.wait_for(
                lambda: not device.manual_active
                and controller.maintenance.snapshot.manual_motion == ManualMotionState.DEADMAN_STOP
            )
        )
        self.assertTrue(device.manual_keepalive_received)
        self.assertTrue(
            all(
                direction == "down" and not accepted
                for direction, _, accepted in device.manual_keepalive_received
            )
        )

    def test_upper_and_lower_limits_block_only_their_commanded_direction(self) -> None:
        controller, device = self.create_connected_controller(
            initially_homed=True,
            upper_active=True,
            lower_active=False,
        )
        self.run_system_check(controller)
        window = MainWindow(controller)
        page = window.maintenance_page

        self.assertFalse(page.manual_up_button.isEnabled())
        self.assertTrue(page.manual_down_button.isEnabled())
        with self.assertRaisesRegex(RuntimeError, "up travel limit"):
            controller.maintenance.start_manual_motion("up")

        page.manual_down_button.pressed.emit()
        self.assertTrue(
            self.wait_for(lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_DOWN)
        )
        device.lower_active = True
        self.assertTrue(
            self.wait_for(
                lambda: not device.manual_active
                and page.maintenance.snapshot.manual_motion == ManualMotionState.SAFETY_STOP
            )
        )
        self.assertIn("lower limit", page.maintenance.snapshot.manual_motion_message)
        window.close()

    def test_lower_limit_only_blocks_downward_manual_motion(self) -> None:
        controller, device = self.create_connected_controller(
            initially_homed=True,
            upper_active=False,
            lower_active=True,
        )
        self.run_system_check(controller)
        window = MainWindow(controller)
        page = window.maintenance_page

        self.assertTrue(page.manual_up_button.isEnabled())
        self.assertFalse(page.manual_down_button.isEnabled())
        with self.assertRaisesRegex(RuntimeError, "down travel limit"):
            controller.maintenance.start_manual_motion("down")
        page.manual_up_button.pressed.emit()
        self.assertTrue(
            self.wait_for(lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_UP)
        )
        self.assertEqual(device.manual_direction, "up")
        controller.maintenance.stop_manual_motion("test_release")
        self.assertTrue(
            self.wait_for(
                lambda: not device.manual_active
                and controller.maintenance.snapshot.manual_motion == ManualMotionState.IDLE
            )
        )
        window.close()

    def test_leaving_maintenance_stops_manual_motion(self) -> None:
        controller, device = self.create_connected_controller(initially_homed=True)
        self.run_system_check(controller)
        window = MainWindow(controller)
        window.show()
        self.qt_app.processEvents()
        window.maintenance_navigation_button.click()
        page = window.maintenance_page
        page.manual_up_button.pressed.emit()
        self.assertTrue(self.wait_for(lambda: device.manual_active))

        window.monitor_navigation_button.click()

        self.assertTrue(
            self.wait_for(
                lambda: not device.manual_active
                and page.maintenance.snapshot.manual_motion == ManualMotionState.IDLE
            )
        )
        window.close()

    def test_application_deactivation_stops_manual_motion(self) -> None:
        controller, device = self.create_connected_controller(initially_homed=True)
        self.run_system_check(controller)
        window = MainWindow(controller)
        page = window.maintenance_page
        controller.maintenance.start_manual_motion("down")
        self.assertTrue(self.wait_for(lambda: device.manual_active))

        page._on_application_state_changed(Qt.ApplicationState.ApplicationInactive)

        self.assertTrue(
            self.wait_for(
                lambda: not device.manual_active
                and controller.maintenance.snapshot.manual_motion == ManualMotionState.IDLE
            )
        )
        window.close()

    def test_controller_shutdown_requests_manual_stop_before_closing_serial(self) -> None:
        controller, device = self.create_connected_controller(initially_homed=True)
        self.run_system_check(controller)
        controller.maintenance.start_manual_motion("up")
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_UP
            )
        )

        controller.shutdown()

        self.assertFalse(device.manual_active)
        self.assertTrue(any(write.endswith(b" manual_stop\r\n") for write in device.writes))

    def test_failed_manual_stop_does_not_retry_forever_and_deadman_stops_fake_device(self) -> None:
        controller, device = self.create_connected_controller(
            initially_homed=True,
            drop_manual_stop=True,
            manual_deadman_timeout_s=0.25,
        )
        self.run_system_check(controller)
        controller.maintenance.start_manual_motion("up")
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_UP
            )
        )

        controller.maintenance.stop_manual_motion("test_release")
        self.assertTrue(
            self.wait_for(
                lambda: not device.manual_active
                and controller.maintenance.snapshot.manual_motion == ManualMotionState.COMMUNICATION_STOP,
                timeout_s=1.0,
            )
        )
        self.assertEqual(
            sum(write.endswith(b" manual_stop\r\n") for write in device.writes),
            1,
        )

    def test_firmware_fault_event_latches_readiness_and_stops_manual_motion(self) -> None:
        controller, device = self.create_connected_controller(initially_homed=True)
        self.run_system_check(controller)
        controller.maintenance.start_manual_motion("up")
        self.assertTrue(self.wait_for(lambda: device.manual_active))

        controller.serial_client.device_event_received.emit(
            DeviceEvent("fault_appeared", {"code": "upper_limit"}, "event: type=fault_appeared")
        )

        self.assertTrue(
            self.wait_for(
                lambda: not device.manual_active
                and controller.maintenance.snapshot.manual_motion == ManualMotionState.FAULT
            )
        )
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.FAULT)
        self.assertTrue(controller.state.snapshot.hardware_status.fault_known)
        self.assertIn("upper_limit", controller.state.snapshot.hardware_status.fault)


if __name__ == "__main__":
    unittest.main()
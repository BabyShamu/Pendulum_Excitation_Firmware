import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.maintenance import (
    BitResultState,
    CheckName,
    CheckState,
    ManualMotionState,
    ReadinessState,
)
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    HardwareStatus,
    HomeStatus,
    MotionState,
    TelemetrySample,
)
from pendulum_workbench.infrastructure.serial_client import SerialTelemetryClient
from pendulum_workbench.infrastructure.firmware_protocol import DeviceEvent
from pendulum_workbench.ui.main_window import MainWindow


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
        self.angle_available = angle_available
        self.defer_status = defer_status
        self.defer_home = defer_home
        self.prepend_stale_response = prepend_stale_response
        self.manual_deadman_timeout_s = manual_deadman_timeout_s
        self.drop_manual_keepalives = drop_manual_keepalives
        self.drop_manual_stop = drop_manual_stop
        self.upper_active = upper_active
        self.lower_active = lower_active
        self.manual_active = False
        self.manual_direction: str | None = None
        self.last_manual_keepalive = 0.0
        self.pending_status_id: str | None = None
        self.pending_home_id: str | None = None
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
                self.last_manual_keepalive = time.monotonic()
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
            self.last_manual_keepalive = time.monotonic()
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
        return controller, serial_instances[0]

    def run_system_check(self, controller: WorkbenchController) -> None:
        controller.maintenance.run_system_check()
        self.assertTrue(
            self.wait_for(
                lambda: controller.maintenance.snapshot.bit_state
                in (BitResultState.PASSED, BitResultState.FAILED)
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
        controller, _device = self.create_connected_controller(fault_code=3)
        events = []
        controller.state.event_added.connect(events.append)

        self.run_system_check(controller)

        snapshot = controller.maintenance.snapshot
        self.assertEqual(snapshot.bit_state, BitResultState.FAILED)
        self.assertEqual(snapshot.readiness, ReadinessState.FAULT)
        self.assertEqual(snapshot.checks[-1].state, CheckState.FAIL)
        self.assertTrue(any(event.severity.value == "Error" for event in events))

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
        self.assertTrue(
            self.wait_for(lambda: any(b"manual_keepalive_up" in write for write in device.writes))
        )
        self.qt_app.processEvents()
        self.assertFalse(any("manual_keepalive" in event.message for event in events))

        controller.state.publish_sample(TelemetrySample(201, 4.02, 1.0, 6.25, source="real"))
        self.assertIn("6.25 mm", page.manual_position_value.text())
        QTest.mouseRelease(page.manual_up_button, Qt.MouseButton.LeftButton)
        self.assertTrue(
            self.wait_for(lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.IDLE)
        )
        self.assertFalse(device.manual_active)
        self.assertTrue(any(write.endswith(b" manual_stop\r\n") for write in device.writes))
        self.assertTrue(self.wait_for(lambda: not controller.firmware_commands.is_busy))

        QTest.mousePress(page.manual_down_button, Qt.MouseButton.LeftButton)
        self.assertTrue(
            self.wait_for(lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.MOVING_DOWN)
        )
        self.assertEqual(device.manual_direction, "down")
        QTest.mouseRelease(page.manual_down_button, Qt.MouseButton.LeftButton)
        self.assertTrue(
            self.wait_for(lambda: page.maintenance.snapshot.manual_motion == ManualMotionState.IDLE)
        )
        self.assertFalse(device.manual_active)
        window.close()

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
                and page.maintenance.snapshot.manual_motion == ManualMotionState.FAULT,
                timeout_s=1.0,
            )
        )
        self.assertFalse(device.manual_active)
        window.close()

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
                and page.maintenance.snapshot.manual_motion == ManualMotionState.IDLE
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
        self.assertTrue(self.wait_for(lambda: not device.manual_active))
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
                and controller.maintenance.snapshot.manual_motion == ManualMotionState.FAULT,
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
                and controller.maintenance.snapshot.manual_motion == ManualMotionState.IDLE
            )
        )
        self.assertEqual(controller.maintenance.snapshot.readiness, ReadinessState.FAULT)
        self.assertTrue(controller.state.snapshot.hardware_status.fault_known)
        self.assertIn("upper_limit", controller.state.snapshot.hardware_status.fault)


if __name__ == "__main__":
    unittest.main()
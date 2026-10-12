import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtWidgets import QApplication, QMessageBox

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.maintenance import (
    BitResultState,
    CheckName,
    CheckState,
    ReadinessState,
)
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    HardwareStatus,
    HomeStatus,
    MotionState,
)
from pendulum_workbench.infrastructure.serial_client import SerialTelemetryClient
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
            angle_ok = int(self.angle_available)
            response = (
                f"maintenance: id={request_id} type=status ok=1 angle_ok={angle_ok} "
                f"angle_raw=2048 angle_deg={0.0 if self.zeroed else 2.5:.2f} "
                f"run=0 sine=0 parametric=0 closed_loop=0 homed={int(self.homed)} "
                f"upper=0 lower=0 fault={self.fault_code} span_steps={1200 if self.homed else 0} "
                "position_steps=0 steps_per_mm=40.000 position_mm=0.000\r\n"
            )
        elif command == "switches":
            response = f"maintenance: id={request_id} type=switches ok=1 upper=0 lower=0\r\n"
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
        controller, _device = self.create_connected_controller(angle_available=False)
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
        window.close()

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
        window = MainWindow(controller)
        controller.state.set_hardware_status(
            HardwareStatus(active_motion_mode="Sine", homed=True, fault_known=True)
        )

        self.assertFalse(window.maintenance_page.home_button.isEnabled())
        self.assertFalse(window.maintenance_page.zero_angle_button.isEnabled())
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
        self.assertTrue(any(write == b"maint 4 home\r\n" for write in device.writes))
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


if __name__ == "__main__":
    unittest.main()
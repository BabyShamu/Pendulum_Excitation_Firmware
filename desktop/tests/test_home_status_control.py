import unittest

from PySide6.QtWidgets import QApplication

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import HomeStatus
from pendulum_workbench.ui.home_status_control import HomeStatusControl


class HomeStatusControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_all_device_home_states_are_visual_only_and_accessible(self) -> None:
        control = HomeStatusControl()
        expected = (
            HomeStatus.UNKNOWN,
            HomeStatus.HOME_REQUIRED,
            HomeStatus.HOMING,
            HomeStatus.HOMED,
            HomeStatus.HOMING_FAILED,
        )

        for status in expected:
            with self.subTest(status=status):
                control.set_home_status(status)
                self.assertEqual(control.status, status)
                self.assertEqual(control.state_label.text(), status.value)
                self.assertEqual(
                    control.accessibleName(), f"Device home status: {status.value}"
                )
            self.assertIn("border-radius: 10px", control.styleSheet())
            self.assertIn("Read-only homing status", control.toolTip())
            self.assertGreaterEqual(control.minimumHeight(), 60)
            self.assertEqual(control.layout().count(), 1)
            self.assertNotIn("HOME:", control.state_label.text())
        self.assertFalse(hasattr(control, "clicked"))

    def test_existing_firmware_messages_update_home_state_without_connecting(self) -> None:
        controller = WorkbenchController()
        messages = (
            ("homing: moving to lower limit", HomeStatus.HOMING),
            ("homing complete: midpoint reached", HomeStatus.HOMED),
            ("homing failed: upper limit not found", HomeStatus.HOMING_FAILED),
        )

        for message, expected in messages:
            with self.subTest(message=message):
                controller._on_device_message(message)
                self.assertEqual(controller.state.snapshot.home_status, expected)
                self.assertFalse(controller.serial_client.is_running)


if __name__ == "__main__":
    unittest.main()
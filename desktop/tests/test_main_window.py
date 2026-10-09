import unittest

from PySide6.QtWidgets import QApplication

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import ConnectionState, RecordingState
from pendulum_workbench.infrastructure.mock_telemetry import MockTelemetrySource
from pendulum_workbench.ui.main_window import MainWindow


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_dashboard_displays_mock_state_samples_and_events(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        controller.start_mock()

        self.assertEqual(window.connection_value[1].text(), ConnectionState.DISCONNECTED.value)
        self.assertEqual(window.recording_value[1].text(), RecordingState.OFF.value)
        self.assertIn("MOCK SOURCE · LIVE", window.mock_badge.text())
        self.assertEqual(window.samples_value[1].text(), "1")
        self.assertEqual(window.event_list.count(), 1)
        self.assertIn("Mock telemetry started", window.event_list.item(0).text())
        self.assertEqual(window.event_list.item(0).foreground().color().name(), "#277a68")

        controller.stop_mock()
        self.assertEqual(window.event_list.count(), 2)
        window.close()


if __name__ == "__main__":
    unittest.main()
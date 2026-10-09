import unittest
from datetime import datetime, timezone

from PySide6.QtWidgets import QApplication, QPushButton

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    HardwareStatus,
    RecordingState,
    TelemetrySample,
)
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
        self.assertIn("MOCK DATA · LIVE", window.source_badge.text())
        self.assertEqual(window.samples_value[1].text(), "1")
        self.assertEqual(window.event_list.count(), 1)
        self.assertFalse(window.connect_button.isEnabled())
        self.assertEqual(window.source_combo.currentData(), DataSourceMode.MOCK)
        self.assertFalse(
            any(
                button.text().lower() in {"home", "start sine", "parametric", "stop"}
                for button in window.findChildren(QPushButton)
            )
        )
        self.assertIn("Mock telemetry started", window.event_list.item(0).text())
        self.assertEqual(window.event_list.item(0).foreground().color().name(), "#277a68")

        controller.stop_mock()
        self.assertEqual(window.event_list.count(), 2)
        window.close()

    def test_real_mode_is_visually_distinct_and_does_not_run_mock_data(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        controller.start_mock()

        controller.select_data_source(DataSourceMode.REAL)

        self.assertIn("REAL STM32 · DISCONNECTED", window.source_badge.text())
        self.assertEqual(window.source_combo.currentData(), DataSourceMode.REAL)
        self.assertEqual(controller.state.snapshot.samples_received, 0)
        self.assertEqual(controller.state.snapshot.connection, ConnectionState.DISCONNECTED)
        self.assertFalse(controller.mock_source.is_active)
        self.assertEqual(len(window._samples), 0)
        self.assertEqual(
            window.connect_button.isEnabled(),
            window.port_combo.currentData() is not None,
        )
        window.close()

    def test_real_hardware_status_and_receive_time_are_displayed(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        controller.select_data_source(DataSourceMode.REAL)
        timestamp = datetime(2026, 10, 9, 12, 30, tzinfo=timezone.utc)
        controller.state.set_hardware_status(
            HardwareStatus(
                homed=True,
                upper_limit=False,
                lower_limit=True,
                active_motion_mode="Idle",
                fault_known=True,
                updated_at=timestamp,
            )
        )
        controller.state.publish_sample(
            TelemetrySample(1, 0.25, 3.0, 1.5, "real", received_at=timestamp)
        )

        self.assertEqual(window.homed_value[1].text(), "Yes")
        self.assertEqual(window.upper_limit_value[1].text(), "Clear")
        self.assertEqual(window.lower_limit_value[1].text(), "Pressed")
        self.assertEqual(window.fault_value[1].text(), "No fault")
        self.assertEqual(window.angle_value[1].text(), "+3.00")
        expected_time = timestamp.astimezone().strftime("%H:%M:%S.%f")[:-3]
        self.assertEqual(window.last_telemetry_value[1].text(), expected_time)

        controller.state.set_telemetry_stale(True)
        self.assertTrue(window.last_telemetry_value[1].text().startswith("STALE"))
        window.close()


if __name__ == "__main__":
    unittest.main()
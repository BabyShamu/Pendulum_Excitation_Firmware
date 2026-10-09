import unittest
from datetime import datetime, timezone

from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    HardwareStatus,
    HomeStatus,
    TelemetrySample,
)
from pendulum_workbench.infrastructure.mock_telemetry import MockTelemetrySource
from pendulum_workbench.ui.home_status_control import HomeStatusControl
from pendulum_workbench.ui.hardware_status_card import HardwareStatusCard
from pendulum_workbench.ui.main_window import MainWindow
from pendulum_workbench.ui.status_indicator import IndicatorTone, StatusChip


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_dashboard_displays_mock_state_samples_and_events(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        controller.start_mock()

        self.assertEqual(window.connection_value.state_text, "DEVICE: DISCONNECTED")
        self.assertEqual(window.recording_value.state_text, "RECORDING: OFF")
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
        self.assertIsInstance(window.connection_value, StatusChip)
        self.assertIsInstance(window.upper_limit_value, HardwareStatusCard)
        self.assertIsInstance(window.lower_limit_value, HardwareStatusCard)
        self.assertIsInstance(window.fault_value, HardwareStatusCard)
        self.assertIsInstance(window.recording_value, StatusChip)
        self.assertIsInstance(window.motion_value, StatusChip)
        self.assertIsInstance(window.home_status_control, HomeStatusControl)
        self.assertEqual(window.upper_limit_value.title_label.text(), "UPPER LIMIT")
        self.assertEqual(window.upper_limit_value.state_text, "UNKNOWN")
        self.assertEqual(window.lower_limit_value.title_label.text(), "LOWER LIMIT")
        self.assertEqual(window.lower_limit_value.state_text, "UNKNOWN")
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

    def test_text_widgets_blend_with_white_surface_cards(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        style = window.styleSheet()

        self.assertIn("QWidget { background: transparent;", style)
        self.assertIn("QLabel { background: transparent;", style)
        self.assertIn("QFrame#surface { background: #ffffff;", style)
        self.assertEqual(window.findChild(QWidget, "centralContent").objectName(), "centralContent")
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

        self.assertEqual(window.home_status_control.status, HomeStatus.HOMED)
        self.assertEqual(window.upper_limit_value.title_label.text(), "UPPER LIMIT")
        self.assertEqual(window.upper_limit_value.state_text, "CLEAR")
        self.assertEqual(window.lower_limit_value.title_label.text(), "LOWER LIMIT")
        self.assertEqual(window.lower_limit_value.state_text, "ACTIVE")
        self.assertIn("Active", window.lower_limit_value.accessibleName())
        self.assertEqual(
            window.lower_limit_value.accessibleDescription(),
            "Hardware Lower Limit status: Active.",
        )
        self.assertEqual(window.fault_value.title_label.text(), "FAULT")
        self.assertEqual(window.fault_value.state_text, "NO FAULT")
        self.assertEqual(window.connection_value.state_text, "DEVICE: DISCONNECTED")
        self.assertEqual(window.recording_value.state_text, "RECORDING: OFF")
        self.assertEqual(window.active_mode_value[1].text(), "Idle")
        self.assertEqual(window.angle_value[1].text(), "+3.00")
        expected_time = timestamp.astimezone().strftime("%H:%M:%S.%f")[:-3]
        self.assertEqual(window.last_telemetry_value[1].text(), expected_time)

        controller.state.set_telemetry_stale(True)
        self.assertTrue(window.last_telemetry_value[1].text().startswith("STALE"))
        window.close()

    def test_hardware_status_rows_are_separated_and_cards_are_equal(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        window.show()
        self.qt_app.processEvents()

        self.assertGreaterEqual(window.hardware_panel.height(), 232)
        self.assertEqual(
            len({
                (card.width(), card.height())
                for card in (
                    window.upper_limit_value,
                    window.lower_limit_value,
                    window.fault_value,
                )
            }),
            1,
        )
        middle_bottom = max(
            card.geometry().bottom()
            for card in (
                window.upper_limit_value,
                window.lower_limit_value,
                window.fault_value,
            )
        )
        self.assertGreater(window.active_mode_value[0].geometry().top(), middle_bottom)
        self.assertGreater(window.last_telemetry_value[0].geometry().top(), middle_bottom)
        self.assertEqual(
            window.active_mode_value[0].size(),
            window.last_telemetry_value[0].size(),
        )
        self.assertEqual(window.active_mode_value[1].text(), "Unknown")
        self.assertEqual(window.last_telemetry_value[1].text(), "No valid samples")
        window.close()

    def test_menu_actions_match_milestone_three_scope(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        menus = {
            action.text(): {
                menu_action.text(): menu_action
                for menu_action in action.menu().actions()
                if not menu_action.isSeparator()
            }
            for action in window.menuBar().actions()
        }

        self.assertEqual(
            set(menus), {"File", "Device", "View", "Help"}
        )
        self.assertEqual(set(menus["File"]), {"Open Experiment", "Exit"})
        self.assertEqual(set(menus["Device"]), {"Refresh Ports", "Connect", "Disconnect"})
        self.assertEqual(set(menus["View"]), {"Clear Events"})
        self.assertEqual(set(menus["Help"]), {"About"})
        self.assertFalse(any("Home" in name or "Sine" in name or "Parametric" in name for name in menus["Device"]))

        window.event_list.addItem("temporary event")
        menus["View"]["Clear Events"].trigger()
        self.assertEqual(window.event_list.count(), 0)
        self.assertFalse(controller.serial_client.is_running)
        window.close()


if __name__ == "__main__":
    unittest.main()
import unittest
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import (
    ConnectionState,
    DataSourceMode,
    HardwareStatus,
    HomeStatus,
    RecordingState,
    TelemetrySample,
)
from pendulum_workbench.infrastructure.mock_telemetry import MockTelemetrySource
from pendulum_workbench.ui.home_status_control import HomeStatusControl
from pendulum_workbench.ui.hardware_status_card import HardwareStatusCard
from pendulum_workbench.ui.main_window import MainWindow
from pendulum_workbench.ui.status_indicator import IndicatorTone, StatusChip
from pendulum_workbench.experiments.repository import ExperimentRepository


class MainWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, timeout_s: float = 2.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            self.qt_app.processEvents()
            if predicate():
                return True
            time.sleep(0.005)
        self.qt_app.processEvents()
        return predicate()

    def test_dashboard_displays_mock_state_samples_and_events(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        controller.start_mock()

        self.assertEqual(window.connection_value.state_text, "DEVICE: DISCONNECTED")
        self.assertEqual(window.recording_value.state_text, "RECORDING: OFF")
        self.assertIn("MOCK DATA · LIVE", window.source_badge.text())
        self.assertEqual(window.live_page.samples_value[1].text(), "1")
        self.assertEqual(window.live_page.event_list.count(), 1)
        self.assertFalse(window.live_page.connect_button.isEnabled())
        self.assertEqual(window.live_page.source_combo.currentData(), DataSourceMode.MOCK)
        self.assertFalse(
            any(
                button.text().lower() in {"home", "start sine", "parametric", "stop"}
                for button in window.findChildren(QPushButton)
            )
        )
        self.assertIn("Mock telemetry started", window.live_page.event_list.item(0).text())
        self.assertEqual(window.live_page.event_list.item(0).foreground().color().name(), "#277a68")

        controller.stop_mock()
        self.assertEqual(window.live_page.event_list.count(), 2)
        self.assertIsInstance(window.connection_value, StatusChip)
        self.assertIsInstance(window.live_page.upper_limit_value, HardwareStatusCard)
        self.assertIsInstance(window.live_page.lower_limit_value, HardwareStatusCard)
        self.assertIsInstance(window.live_page.fault_value, HardwareStatusCard)
        self.assertIsInstance(window.recording_value, StatusChip)
        self.assertIsInstance(window.motion_value, StatusChip)
        self.assertIsInstance(window.live_page.home_status_control, HomeStatusControl)
        self.assertEqual(window.live_page.upper_limit_value.title_label.text(), "UPPER LIMIT")
        self.assertEqual(window.live_page.upper_limit_value.state_text, "UNKNOWN")
        self.assertEqual(window.live_page.lower_limit_value.title_label.text(), "LOWER LIMIT")
        self.assertEqual(window.live_page.lower_limit_value.state_text, "UNKNOWN")
        self.assertFalse(window.experiment_page.open_recording_folder_button.isEnabled())
        window.close()

    def test_shell_navigation_preserves_shared_controller_state_and_live_widgets(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        self.addCleanup(controller.shutdown)
        controller.select_data_source(DataSourceMode.REAL)
        controller.state.set_connection(ConnectionState.CONNECTED, "COM6")
        window = MainWindow(controller)

        self.assertEqual(window.page_stack.count(), 2)
        self.assertIs(window.page_stack.currentWidget(), window.live_page)
        self.assertTrue(window.live_navigation_button.isChecked())
        self.assertEqual(window.live_navigation_button.text(), "Live")
        self.assertEqual(window.experiment_navigation_button.text(), "Experiment")
        self.assertIs(window.live_page.controller, controller)
        self.assertIs(window.experiment_page.controller, controller)
        self.assertIs(window.live_page.controller.state, controller.state)
        self.assertIs(window.experiment_page.controller.state, controller.state)
        button_texts = [button.text().lower() for button in window.findChildren(QPushButton)]
        forbidden_controls = ("home", "start sine", "parametric", "start motion", "run experiment")
        self.assertFalse(
            any(control in button_text for control in forbidden_controls for button_text in button_texts)
        )

        snapshot = controller.state.snapshot
        serial_client = controller.serial_client
        window.experiment_navigation_button.click()
        self.assertIs(window.page_stack.currentWidget(), window.experiment_page)
        self.assertTrue(window.experiment_navigation_button.isChecked())
        window.live_navigation_button.click()
        self.assertIs(window.page_stack.currentWidget(), window.live_page)
        self.assertEqual(controller.state.snapshot, snapshot)
        self.assertIs(controller.serial_client, serial_client)

        controller.state.publish_sample(TelemetrySample(1, 0.1, 2.5, -1.0, "real"))
        self.assertEqual(window.live_page.angle_value[1].text(), "+2.50")
        self.assertEqual(window.live_page.position_value[1].text(), "-1.00")
        self.assertEqual(window.live_page.samples_value[1].text(), "1")
        window.close()

    def test_navigation_during_active_recording_does_not_stop_writer(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        repository = ExperimentRepository(Path(temporary_directory.name))
        controller = WorkbenchController(
            mock_source=MockTelemetrySource(interval_ms=1000),
            experiment_repository=repository,
        )
        self.addCleanup(controller.shutdown)
        window = MainWindow(controller)
        controller.start_mock()
        experiment_page = window.experiment_page
        experiment_page.experiment_name_input.setText("Navigation continuity")
        experiment_page.start_recording_button.click()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.recording == RecordingState.RECORDING)
        )
        active_session = controller.experiment_recorder.active_session
        self.assertIsNotNone(active_session)

        window.live_navigation_button.click()
        self.assertEqual(controller.state.snapshot.recording, RecordingState.RECORDING)
        self.assertTrue(controller.experiment_recorder.is_recording)
        window.experiment_navigation_button.click()
        self.assertEqual(controller.experiment_recorder.active_session.experiment_id, active_session.experiment_id)

        experiment_page.stop_recording_button.click()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.recording == RecordingState.SAVED)
        )
        self.assertFalse(controller.experiment_recorder.is_recording)
        window.close()

    def test_real_mode_is_visually_distinct_and_does_not_run_mock_data(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        controller.start_mock()

        controller.select_data_source(DataSourceMode.REAL)

        self.assertIn("REAL STM32 · DISCONNECTED", window.source_badge.text())
        self.assertEqual(window.live_page.source_combo.currentData(), DataSourceMode.REAL)
        self.assertEqual(controller.state.snapshot.samples_received, 0)
        self.assertEqual(controller.state.snapshot.connection, ConnectionState.DISCONNECTED)
        self.assertFalse(controller.mock_source.is_active)
        self.assertEqual(len(window.live_page._samples), 0)
        self.assertEqual(
            window.live_page.connect_button.isEnabled(),
            window.live_page.port_combo.currentData() is not None,
        )
        window.close()

    def test_qt_real_source_string_becomes_enum_before_async_recording(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        repository = ExperimentRepository(Path(temporary_directory.name))
        controller = WorkbenchController(
            mock_source=MockTelemetrySource(interval_ms=1000),
            experiment_repository=repository,
        )
        self.addCleanup(controller.shutdown)
        window = MainWindow(controller)
        real_index = window.live_page.source_combo.findData(DataSourceMode.REAL)

        qt_item_data = window.live_page.source_combo.itemData(real_index)
        self.assertIsInstance(qt_item_data, str)
        window.live_page.source_combo.setCurrentIndex(real_index)
        self.assertIs(controller.state.snapshot.data_source, DataSourceMode.REAL)

        controller.state.set_connection(ConnectionState.CONNECTED, "COM6")
        window.experiment_page.experiment_name_input.setText("Real enum regression")
        window.experiment_page.start_recording_button.click()
        self.assertEqual(controller.state.snapshot.recording.value, "Starting")
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.recording.value == "Recording")
        )
        session = controller.experiment_recorder.active_session
        self.assertIsNotNone(session)
        self.assertIs(session.manifest["data_source"]["mode"], DataSourceMode.REAL.value)
        window.experiment_page.stop_recording_button.click()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.recording.value == "Saved")
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

        self.assertEqual(window.live_page.home_status_control.status, HomeStatus.HOMED)
        self.assertEqual(window.live_page.upper_limit_value.title_label.text(), "UPPER LIMIT")
        self.assertEqual(window.live_page.upper_limit_value.state_text, "CLEAR")
        self.assertEqual(window.live_page.lower_limit_value.title_label.text(), "LOWER LIMIT")
        self.assertEqual(window.live_page.lower_limit_value.state_text, "ACTIVE")
        self.assertIn("Active", window.live_page.lower_limit_value.accessibleName())
        self.assertEqual(
            window.live_page.lower_limit_value.accessibleDescription(),
            "Hardware Lower Limit status: Active.",
        )
        self.assertEqual(window.live_page.fault_value.title_label.text(), "FAULT")
        self.assertEqual(window.live_page.fault_value.state_text, "NO FAULT")
        self.assertEqual(window.connection_value.state_text, "DEVICE: DISCONNECTED")
        self.assertEqual(window.recording_value.state_text, "RECORDING: OFF")
        self.assertEqual(window.live_page.active_mode_value[1].text(), "Idle")
        self.assertEqual(window.live_page.angle_value[1].text(), "+3.00")
        expected_time = timestamp.astimezone().strftime("%H:%M:%S.%f")[:-3]
        self.assertEqual(window.live_page.last_telemetry_value[1].text(), expected_time)

        controller.state.set_telemetry_stale(True)
        self.assertTrue(window.live_page.last_telemetry_value[1].text().startswith("STALE"))
        window.close()

    def test_hardware_status_rows_are_separated_and_cards_are_equal(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        window = MainWindow(controller)
        window.show()
        self.qt_app.processEvents()

        self.assertGreaterEqual(window.live_page.hardware_panel.height(), 232)
        self.assertGreaterEqual(
            window.live_page.home_status_control.width(),
            window.live_page.hardware_panel.width() - 32,
        )
        hardware_cards = (
            window.live_page.upper_limit_value,
            window.live_page.lower_limit_value,
            window.live_page.fault_value,
        )
        self.assertLessEqual(
            max(card.width() for card in hardware_cards)
            - min(card.width() for card in hardware_cards),
            1,
        )
        self.assertEqual(len({card.height() for card in hardware_cards}), 1)
        middle_bottom = max(
            card.geometry().bottom() for card in hardware_cards
        )
        self.assertGreater(window.live_page.active_mode_value[0].geometry().top(), middle_bottom)
        self.assertGreater(window.live_page.last_telemetry_value[0].geometry().top(), middle_bottom)
        self.assertEqual(
            window.live_page.active_mode_value[0].size(),
            window.live_page.last_telemetry_value[0].size(),
        )
        self.assertEqual(window.live_page.active_mode_value[1].text(), "Unknown")
        self.assertEqual(window.live_page.last_telemetry_value[1].text(), "No valid samples")
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

        window.live_page.event_list.addItem("temporary event")
        menus["View"]["Clear Events"].trigger()
        self.assertEqual(window.live_page.event_list.count(), 0)
        self.assertFalse(controller.serial_client.is_running)
        window.close()

    def test_recording_can_be_stopped_and_reopened_after_app_restart(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        repository = ExperimentRepository(Path(temporary_directory.name))
        controller = WorkbenchController(
            mock_source=MockTelemetrySource(interval_ms=1000),
            experiment_repository=repository,
        )
        self.addCleanup(controller.shutdown)
        window = MainWindow(controller)
        controller.start_mock()
        experiment_page = window.experiment_page
        experiment_page.experiment_name_input.setText("Free decay check")
        experiment_page.experiment_notes_input.setText("Small-angle release")
        experiment_page.start_recording_button.click()

        self.assertEqual(controller.state.snapshot.recording.value, "Starting")
        self.assertTrue(experiment_page.recording_state_value.text().startswith("Preparing experiment "))
        self.assertIn(str(repository.root), experiment_page.current_experiment_path.text())
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.recording.value == "Recording")
        )
        self.assertEqual(controller.state.snapshot.recording.value, "Recording")
        self.assertTrue(experiment_page.recording_state_value.text().startswith("Recording "))
        self.assertTrue(experiment_page.stop_recording_button.isEnabled())
        self.assertFalse(experiment_page.start_recording_button.isEnabled())
        controller.state.publish_sample(
            TelemetrySample(
                2,
                0.25,
                4.5,
                -1.25,
                source="mock",
                received_at=datetime(2026, 10, 9, 12, tzinfo=timezone.utc),
            )
        )
        session = controller.experiment_recorder.active_session
        self.assertIsNotNone(session)
        experiment_page.stop_recording_button.click()
        self.assertEqual(controller.state.snapshot.recording.value, "Stopping")
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.recording.value == "Saved")
        )
        self.assertEqual(controller.state.snapshot.recording.value, "Saved")
        self.assertTrue(experiment_page.recording_state_value.text().startswith("Completed: "))
        self.assertFalse(experiment_page.saved_experiment_summary.isHidden())
        self.assertTrue(experiment_page.open_recording_folder_button.isEnabled())
        self.assertEqual(experiment_page.saved_name_value.text(), "Free decay check")
        self.assertEqual(experiment_page.saved_notes_value.text(), "Small-angle release")
        self.assertEqual(experiment_page.saved_id_value.text(), session.experiment_id)
        self.assertEqual(experiment_page.saved_path_value.text(), str(session.directory))
        self.assertEqual(experiment_page.saved_status_value.text(), "COMPLETED")
        self.assertEqual(experiment_page.saved_sample_count_value.text(), "1")
        self.assertEqual(experiment_page.saved_data_source_value.text(), "Mock")
        with patch(
            "pendulum_workbench.ui.pages.experiment_page.QDesktopServices.openUrl",
            return_value=True,
        ) as open_url:
            experiment_page.open_recording_folder_button.click()
        open_url.assert_called_once()
        self.assertEqual(Path(open_url.call_args.args[0].toLocalFile()), session.directory)
        window.close()

        reopened_controller = WorkbenchController(
            mock_source=MockTelemetrySource(interval_ms=1000),
            experiment_repository=repository,
        )
        self.addCleanup(reopened_controller.shutdown)
        reopened_window = MainWindow(reopened_controller)
        with patch(
            "pendulum_workbench.ui.pages.experiment_page.QFileDialog.getOpenFileName",
            return_value=(str(session.directory / "manifest.json"), ""),
        ):
            reopened_window.experiment_page.open_experiment_button.click()

        self.assertEqual(reopened_controller.state.snapshot.data_source, DataSourceMode.SAVED)
        self.assertEqual(reopened_window.source_badge.text(), "SAVED EXPERIMENT · Free decay check")
        self.assertEqual(reopened_window.live_page.samples_value[1].text(), "1")
        self.assertEqual(reopened_window.live_page.angle_value[1].text(), "+4.50")
        self.assertEqual(reopened_window.live_page.position_value[1].text(), "-1.25")
        reopened_experiment_page = reopened_window.experiment_page
        self.assertEqual(reopened_experiment_page.experiment_name_input.text(), "Free decay check")
        self.assertEqual(reopened_experiment_page.experiment_notes_input.text(), "Small-angle release")
        self.assertEqual(reopened_experiment_page.saved_name_value.text(), "Free decay check")
        self.assertEqual(reopened_experiment_page.saved_notes_value.text(), "Small-angle release")
        self.assertEqual(reopened_experiment_page.saved_id_value.text(), session.experiment_id)
        self.assertEqual(reopened_experiment_page.saved_path_value.text(), str(session.directory))
        self.assertEqual(reopened_experiment_page.saved_status_value.text(), "COMPLETED")
        self.assertEqual(reopened_experiment_page.saved_sample_count_value.text(), "1")
        self.assertEqual(reopened_experiment_page.saved_data_source_value.text(), "Mock")
        self.assertTrue(reopened_experiment_page.open_recording_folder_button.isEnabled())
        self.assertEqual(reopened_window.live_page._samples[-1].angle_deg, 4.5)
        reopened_window.close()

    def test_start_click_is_responsive_during_folder_creation(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)

        class SlowRepository(ExperimentRepository):
            def __init__(self, root: Path) -> None:
                super().__init__(root)
                self.create_started = threading.Event()
                self.release_create = threading.Event()
                self.finalize_started = threading.Event()
                self.release_finalize = threading.Event()

            def create(self, **kwargs):
                self.create_started.set()
                self.release_create.wait(2.0)
                return super().create(**kwargs)

            def finalize(self, *args, **kwargs):
                self.finalize_started.set()
                self.release_finalize.wait(2.0)
                return super().finalize(*args, **kwargs)

        repository = SlowRepository(Path(temporary_directory.name))
        self.addCleanup(repository.release_create.set)
        self.addCleanup(repository.release_finalize.set)
        controller = WorkbenchController(
            mock_source=MockTelemetrySource(interval_ms=1000),
            experiment_repository=repository,
        )
        self.addCleanup(controller.shutdown)
        window = MainWindow(controller)
        controller.start_mock()
        experiment_page = window.experiment_page
        window.show()
        timer_fired = []
        QTimer.singleShot(0, lambda: timer_fired.append(True))

        click_started = time.monotonic()
        experiment_page.start_recording_button.click()
        click_elapsed = time.monotonic() - click_started
        self.qt_app.processEvents()

        self.assertLess(click_elapsed, 0.5)
        self.assertTrue(repository.create_started.wait(1.0))
        self.assertTrue(timer_fired)
        self.assertEqual(controller.state.snapshot.recording.value, "Starting")
        self.assertTrue(experiment_page.recording_state_value.text().startswith("Preparing experiment "))
        self.assertIn(str(repository.root), experiment_page.current_experiment_path.text())
        self.assertFalse(experiment_page.start_recording_button.isEnabled())
        self.assertFalse(experiment_page.stop_recording_button.isEnabled())

        repository.release_create.set()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.recording.value == "Recording")
        )
        messages = [
            window.live_page.event_list.item(index).text()
            for index in range(window.live_page.event_list.count())
        ]
        self.assertTrue(any("Start Recording clicked" in message for message in messages))
        self.assertTrue(any("session/folder created" in message for message in messages))
        self.assertTrue(any("writer ready" in message for message in messages))
        self.assertTrue(any("RECORDING state entered" in message for message in messages))
        self.assertTrue(experiment_page.stop_recording_button.isEnabled())
        self.assertFalse(experiment_page.start_recording_button.isEnabled())
        stop_timer_events = []
        QTimer.singleShot(0, lambda: stop_timer_events.append(True))
        stop_started = time.monotonic()
        experiment_page.stop_recording_button.click()
        stop_elapsed = time.monotonic() - stop_started
        self.qt_app.processEvents()
        self.assertLess(stop_elapsed, 0.5)
        self.assertTrue(repository.finalize_started.wait(1.0))
        self.assertTrue(stop_timer_events)
        self.assertEqual(controller.state.snapshot.recording.value, "Stopping")
        repository.release_finalize.set()
        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.recording.value == "Saved")
        )
        window.close()

    def test_start_initialization_failure_shows_exception_and_target_path(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)

        class FailingRepository(ExperimentRepository):
            def create(self, **kwargs):
                raise OSError("simulated disk full")

        repository = FailingRepository(Path(temporary_directory.name))
        controller = WorkbenchController(
            mock_source=MockTelemetrySource(interval_ms=1000),
            experiment_repository=repository,
        )
        self.addCleanup(controller.shutdown)
        window = MainWindow(controller)
        controller.start_mock()
        experiment_page = window.experiment_page
        experiment_page.start_recording_button.click()

        self.assertTrue(
            self.wait_for(lambda: controller.state.snapshot.recording.value == "Error")
        )
        self.assertIn("simulated disk full", experiment_page.current_experiment_path.text())
        self.assertIn(str(repository.root), experiment_page.current_experiment_path.text())
        self.assertIn("OSError", experiment_page.current_experiment_path.text())
        self.assertTrue(
            any(
                "simulated disk full" in window.live_page.event_list.item(index).text()
                for index in range(window.live_page.event_list.count())
            )
        )
        self.assertTrue(experiment_page.start_recording_button.isEnabled())
        self.assertFalse(experiment_page.stop_recording_button.isEnabled())
        window.close()


if __name__ == "__main__":
    unittest.main()
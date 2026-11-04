import tempfile
import time
import unittest
from pathlib import Path

from PySide6.QtWidgets import QApplication

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.application.experiment_recording import ExperimentRecordingService
from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.models import RecordingState, TelemetrySample
from pendulum_workbench.domain.motor_configuration import MotorConfiguration
from pendulum_workbench.experiments.repository import ExperimentRepository
from pendulum_workbench.infrastructure.firmware_protocol import FirmwareProtocolParser
from pendulum_workbench.infrastructure.serial_client import SerialTelemetryClient
from pendulum_workbench.ui.pages.experiment_manager_page import ExperimentManagerPage


VERIFIED_FIELDS = {
    "trajectory_max_speed_steps_s": "5000",
    "trajectory_max_speed_mm_s": "125.0",
    "trajectory_acceleration_steps_s2": "600000",
    "trajectory_acceleration_mm_s2": "15000.0",
    "trajectory_start_speed_steps_s": "150",
    "trajectory_start_speed_mm_s": "3.75",
    "steps_per_mm": "40.000",
    "ar_trace_rate_hz": "0",
}


class MotorConfigurationTests(unittest.TestCase):
    def test_verified_snapshot_uses_manifest_schema(self) -> None:
        config = MotorConfiguration.from_response_fields(dict(VERIFIED_FIELDS))

        manifest = config.to_manifest()

        self.assertEqual(
            set(manifest),
            {
                "max_speed_steps_s",
                "max_speed_mm_s",
                "acceleration_steps_s2",
                "acceleration_mm_s2",
                "start_speed_steps_s",
                "start_speed_mm_s",
                "steps_per_mm",
                "ar_trace_rate_hz",
                "source",
                "status",
                "captured_at_utc",
                "error",
            },
        )
        self.assertEqual(manifest["max_speed_steps_s"], 5000.0)
        self.assertEqual(manifest["acceleration_steps_s2"], 600000.0)
        self.assertEqual(manifest["start_speed_steps_s"], 150.0)
        self.assertEqual(manifest["steps_per_mm"], 40.0)
        self.assertEqual(manifest["ar_trace_rate_hz"], 0)
        self.assertEqual(manifest["source"], "stm32_firmware")
        self.assertEqual(manifest["status"], "verified")
        self.assertIsNotNone(manifest["captured_at_utc"])
        self.assertIsNone(manifest["error"])

    def test_missing_fields_mark_configuration_unsupported(self) -> None:
        fields = dict(VERIFIED_FIELDS)
        del fields["trajectory_acceleration_steps_s2"]

        config = MotorConfiguration.from_response_fields(fields)

        self.assertEqual(config.status, "unavailable")
        self.assertEqual(config.error, "unsupported_firmware")
        self.assertIsNone(config.max_speed_steps_s)
        self.assertIsNotNone(config.captured_at_utc)

    def test_unavailable_snapshot_never_invents_values(self) -> None:
        config = MotorConfiguration.unavailable("configuration_request_timeout")
        manifest = config.to_manifest()

        self.assertEqual(manifest["status"], "unavailable")
        self.assertEqual(manifest["error"], "configuration_request_timeout")
        for key in (
            "max_speed_steps_s",
            "max_speed_mm_s",
            "acceleration_steps_s2",
            "acceleration_mm_s2",
            "start_speed_steps_s",
            "start_speed_mm_s",
            "steps_per_mm",
            "ar_trace_rate_hz",
        ):
            self.assertIsNone(manifest[key])

    def test_protocol_parser_accepts_digit_suffixed_config_fields(self) -> None:
        line = (
            "maintenance: id=7 type=config ok=1 "
            + " ".join(f"{key}={value}" for key, value in VERIFIED_FIELDS.items())
        )
        response = FirmwareProtocolParser.parse_maintenance_response(line)

        self.assertIsNotNone(response)
        self.assertEqual(response.request_id, 7)
        self.assertEqual(response.response_type, "config")
        self.assertTrue(response.ok)
        for key in VERIFIED_FIELDS:
            self.assertIn(key, response.fields)


class MotorConfigurationDisplayTests(unittest.TestCase):
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

    def _record_experiment(self, repository_root: Path, metadata: dict) -> None:
        state = ApplicationStateModel()
        state.set_mock_source_active(True)
        repository = ExperimentRepository(repository_root)
        service = ExperimentRecordingService(state, SerialTelemetryClient(), repository)
        self.addCleanup(service.shutdown)
        experiment_id = service.start_recording("Config display", "")
        self.assertTrue(
            self.wait_for(lambda: state.snapshot.recording == RecordingState.RECORDING)
        )
        state.publish_sample(TelemetrySample(1, 0.02, 12.5, 0.0, source="mock"))
        service.update_run_metadata(
            experiment_id,
            name="Config display",
            experiment_metadata=metadata,
        )
        self.assertTrue(
            self.wait_for(
                lambda: service.active_session is not None
                and service.active_session.manifest.get("motor_configuration")
                == metadata.get("motor_configuration")
            )
        )
        service.stop_recording()
        self.assertTrue(
            self.wait_for(lambda: state.snapshot.recording == RecordingState.SAVED)
        )

    def test_library_displays_recorded_motor_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            motor_config = MotorConfiguration.from_response_fields(
                dict(VERIFIED_FIELDS)
            ).to_manifest()
            self._record_experiment(
                Path(temporary_directory),
                {"motor_configuration": motor_config},
            )

            controller = WorkbenchController(
                experiment_repository=ExperimentRepository(Path(temporary_directory))
            )
            page = ExperimentManagerPage(controller)
            self.addCleanup(controller.shutdown)
            self.addCleanup(page.close)
            page.ensure_loaded()  # triggers the deferred initial load
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))
            page.table.selectRow(0)

            self.assertEqual(
                page.detail_labels["Max motor speed"].text(), "5,000 steps/s"
            )
            self.assertEqual(
                page.detail_labels["Carriage velocity"].text(), "125.0 mm/s"
            )
            self.assertEqual(
                page.detail_labels["Acceleration"].text(), "600,000 steps/s²"
            )
            self.assertEqual(
                page.detail_labels["Carriage acceleration"].text(), "15,000 mm/s²"
            )
            self.assertEqual(page.detail_labels["Starting speed"].text(), "150 steps/s")
            self.assertEqual(
                page.detail_labels["Motor calibration"].text(), "40.000 steps/mm"
            )
            self.assertEqual(page.detail_labels["AR diagnostic rate"].text(), "0 Hz")
            self.assertEqual(page.detail_labels["Config status"].text(), "verified")

    def test_library_marks_historical_experiments_as_not_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            self._record_experiment(Path(temporary_directory), {})

            controller = WorkbenchController(
                experiment_repository=ExperimentRepository(Path(temporary_directory))
            )
            page = ExperimentManagerPage(controller)
            self.addCleanup(controller.shutdown)
            self.addCleanup(page.close)
            page.ensure_loaded()  # triggers the deferred initial load
            self.assertTrue(self.wait_for(lambda: page.table.rowCount() == 1))
            page.table.selectRow(0)

            for key in (
                "Max motor speed",
                "Carriage velocity",
                "Acceleration",
                "Carriage acceleration",
                "Starting speed",
                "Motor calibration",
                "AR diagnostic rate",
                "Config status",
            ):
                self.assertEqual(page.detail_labels[key].text(), "Not recorded")


if __name__ == "__main__":
    unittest
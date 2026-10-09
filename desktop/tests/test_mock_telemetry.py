import unittest

from PySide6.QtWidgets import QApplication

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import (
    ConnectionState,
    EventSeverity,
    TelemetrySample,
)
from pendulum_workbench.infrastructure.mock_telemetry import MockTelemetrySource


class MockTelemetryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QApplication.instance() or QApplication([])

    def test_start_emits_repeatable_sample_without_claiming_device_connection(self) -> None:
        controller = WorkbenchController(mock_source=MockTelemetrySource(interval_ms=1000))
        samples: list[TelemetrySample] = []
        events = []
        controller.state.telemetry_received.connect(samples.append)
        controller.state.event_added.connect(events.append)

        controller.start_mock()
        controller.stop_mock()

        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].sample_index, 1)
        self.assertEqual(samples[0].elapsed_s, 0.0)
        self.assertEqual(controller.state.snapshot.connection, ConnectionState.DISCONNECTED)
        self.assertFalse(controller.state.snapshot.mock_source_active)
        self.assertTrue(any(event.severity == EventSeverity.WARNING for event in events))

    def test_nonpositive_interval_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MockTelemetrySource(interval_ms=0)


if __name__ == "__main__":
    unittest.main()
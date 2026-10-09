import unittest

from PySide6.QtCore import QCoreApplication

from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.models import (
    ConnectionState,
    EventSeverity,
    HardwareStatus,
    MotionState,
    RecordingState,
    TelemetrySample,
)


class ApplicationStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.qt_app = QCoreApplication.instance() or QCoreApplication([])

    def test_motion_and_recording_states_are_independent(self) -> None:
        state = ApplicationStateModel()
        state.set_motion(MotionState.RUNNING)
        state.set_recording(RecordingState.RECORDING)

        self.assertEqual(state.snapshot.motion, MotionState.RUNNING)
        self.assertEqual(state.snapshot.recording, RecordingState.RECORDING)
        self.assertEqual(state.snapshot.connection, ConnectionState.DISCONNECTED)

    def test_publishing_sample_updates_snapshot_and_emits_sample(self) -> None:
        state = ApplicationStateModel()
        received = []
        state.telemetry_received.connect(received.append)
        sample = TelemetrySample(1, 0.1, 2.5, -1.0)

        state.publish_sample(sample)

        self.assertEqual(state.snapshot.samples_received, 1)
        self.assertEqual(state.snapshot.latest_sample, sample)
        self.assertEqual(received, [sample])

    def test_switch_updates_preserve_unknown_fault_and_other_status_fields(self) -> None:
        state = ApplicationStateModel()
        state.set_hardware_status(
            HardwareStatus(upper_limit=True, lower_limit=False)
        )

        status = state.snapshot.hardware_status
        self.assertTrue(status.upper_limit)
        self.assertFalse(status.lower_limit)
        self.assertIsNone(status.homed)
        self.assertFalse(status.fault_known)
        self.assertEqual(status.active_motion_mode, "Unknown")

    def test_event_is_timestamped_and_emitted(self) -> None:
        state = ApplicationStateModel()
        received = []
        state.event_added.connect(received.append)

        state.report_event(EventSeverity.WARNING, "Mock stream paused")

        self.assertEqual(received[0].severity, EventSeverity.WARNING)
        self.assertEqual(received[0].message, "Mock stream paused")
        self.assertIsNotNone(received[0].timestamp.tzinfo)


if __name__ == "__main__":
    unittest.main()
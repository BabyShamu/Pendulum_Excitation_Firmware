from dataclasses import replace

from PySide6.QtCore import QObject, Signal

from pendulum_workbench.domain.models import (
    AppEvent,
    ApplicationSnapshot,
    ConnectionState,
    EventSeverity,
    MotionState,
    RecordingState,
    TelemetrySample,
)


class ApplicationStateModel(QObject):
    snapshot_changed = Signal(object)
    telemetry_received = Signal(object)
    event_added = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._snapshot = ApplicationSnapshot()

    @property
    def snapshot(self) -> ApplicationSnapshot:
        return self._snapshot

    def set_mock_source_active(self, active: bool) -> None:
        self._update(mock_source_active=active)

    def set_connection(self, state: ConnectionState) -> None:
        self._update(connection=state)

    def set_motion(self, state: MotionState) -> None:
        self._update(motion=state)

    def set_recording(self, state: RecordingState) -> None:
        self._update(recording=state)

    def publish_sample(self, sample: TelemetrySample) -> None:
        self._update(
            samples_received=self._snapshot.samples_received + 1,
            latest_sample=sample,
        )
        self.telemetry_received.emit(sample)

    def report_event(self, severity: EventSeverity, message: str) -> None:
        self.event_added.emit(AppEvent.now(severity, message))

    def _update(self, **changes: object) -> None:
        self._snapshot = replace(self._snapshot, **changes)
        self.snapshot_changed.emit(self._snapshot)
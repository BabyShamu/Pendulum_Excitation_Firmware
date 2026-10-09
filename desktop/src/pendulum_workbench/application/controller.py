from PySide6.QtCore import QObject

from pendulum_workbench.application.state import ApplicationStateModel
from pendulum_workbench.domain.models import EventSeverity
from pendulum_workbench.infrastructure.mock_telemetry import MockTelemetrySource


class WorkbenchController(QObject):
    def __init__(
        self,
        state: ApplicationStateModel | None = None,
        mock_source: MockTelemetrySource | None = None,
    ) -> None:
        super().__init__()
        self.state = state or ApplicationStateModel()
        self.mock_source = mock_source or MockTelemetrySource()
        self.mock_source.sample_ready.connect(self.state.publish_sample)

    def start_mock(self) -> None:
        if self.mock_source.is_active:
            return
        self.state.set_mock_source_active(True)
        self.state.report_event(
            EventSeverity.INFO,
            "Mock telemetry started; no STM32 connection is active.",
        )
        self.mock_source.start()

    def stop_mock(self) -> None:
        if not self.mock_source.is_active:
            return
        self.mock_source.stop()
        self.state.set_mock_source_active(False)
        self.state.report_event(EventSeverity.WARNING, "Mock telemetry paused.")

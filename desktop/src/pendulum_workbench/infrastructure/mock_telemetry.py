import math

from PySide6.QtCore import QObject, QTimer, Signal

from pendulum_workbench.domain.models import TelemetrySample


class MockTelemetrySource(QObject):
    sample_ready = Signal(object)

    def __init__(self, interval_ms: int = 100) -> None:
        super().__init__()
        if interval_ms <= 0:
            raise ValueError("interval_ms must be positive")

        self._interval_ms = interval_ms
        self._sample_index = 0
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self._publish_next_sample)

    @property
    def is_active(self) -> bool:
        return self._timer.isActive()

    def start(self) -> None:
        if self.is_active:
            return
        self._publish_next_sample()
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()

    def _publish_next_sample(self) -> None:
        self._sample_index += 1
        elapsed_s = (self._sample_index - 1) * self._interval_ms / 1000.0
        phase = 0.6 * elapsed_s
        self.sample_ready.emit(
            TelemetrySample(
                sample_index=self._sample_index,
                elapsed_s=elapsed_s,
                angle_deg=16.0 * math.sin(phase),
                position_mm=6.0 * math.cos(phase),
            )
        )
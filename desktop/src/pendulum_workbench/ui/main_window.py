from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import AppEvent, ApplicationSnapshot, EventSeverity, TelemetrySample
from pendulum_workbench.ui.trend_plot import TrendPlot


class MainWindow(QMainWindow):
    def __init__(self, controller: WorkbenchController) -> None:
        super().__init__()
        self.controller = controller
        self._samples: deque[TelemetrySample] = deque(maxlen=180)
        self.setWindowTitle("Pendulum Workbench")
        self.resize(1120, 760)
        self.setMinimumSize(820, 600)
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f1f3ef; color: #24363a; }
            QLabel#eyebrow { color: #287b68; font-size: 10px; font-weight: 700; }
            QLabel#title { color: #203238; font-size: 27px; font-weight: 700; }
            QLabel#subtle { color: #71807d; font-size: 11px; }
            QFrame#surface { background: #ffffff; border: 1px solid #dce4df; border-radius: 5px; }
            QLabel#sectionTitle { color: #50615d; font-size: 10px; font-weight: 700; }
            QLabel#metricValue { color: #203238; font-size: 23px; font-weight: 600; }
            QLabel#metricUnit { color: #76837f; font-size: 10px; }
            QLabel#stateValue { color: #24363a; font-size: 12px; font-weight: 600; }
            QLabel#mockBadge { color: #a8512c; background: #fff0e7; padding: 6px 9px; font-weight: 700; }
            QPushButton { background: #247a68; color: white; border: 0; padding: 9px 14px; font-weight: 600; }
            QPushButton:hover { background: #1b6455; }
            QListWidget { background: #ffffff; border: 0; }
            """
        )

        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        self.setCentralWidget(root)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        eyebrow = QLabel("INSTRUMENTATION  /  MILESTONE 2")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("Pendulum Workbench")
        title.setObjectName("title")
        subtitle = QLabel("Live telemetry shell · no hardware control enabled")
        subtitle.setObjectName("subtle")
        titles.addWidget(eyebrow)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        self.mock_badge = QLabel("MOCK SOURCE · PAUSED")
        self.mock_badge.setObjectName("mockBadge")
        self.mock_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mock_badge.setObjectName("mockBadge")
        self.mock_badge.setAccessibleName("Mock telemetry source status")
        header.addWidget(self.mock_badge, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        state_panel = QFrame()
        state_panel.setObjectName("surface")
        state_layout = QHBoxLayout(state_panel)
        state_layout.setContentsMargins(16, 10, 16, 10)
        state_layout.setSpacing(28)
        self.connection_value = self._state_pair("DEVICE", "Disconnected")
        self.motion_value = self._state_pair("MOTION", "Unknown")
        self.recording_value = self._state_pair("RECORDING", "Off")
        for pair in (self.connection_value, self.motion_value, self.recording_value):
            state_layout.addLayout(pair[0])
        state_layout.addStretch(1)
        self.mock_toggle_button = QPushButton("Resume mock")
        self.mock_toggle_button.setObjectName("mockToggleButton")
        self.mock_toggle_button.clicked.connect(self._toggle_mock)
        state_layout.addWidget(self.mock_toggle_button)
        layout.addWidget(state_panel)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.angle_value = self._metric("PENDULUM ANGLE", "deg")
        self.position_value = self._metric("PIVOT POSITION", "mm")
        self.samples_value = self._metric("SAMPLES RECEIVED", "mock samples")
        for metric in (self.angle_value, self.position_value, self.samples_value):
            metrics.addWidget(metric[0], 1)
        layout.addLayout(metrics)

        content = QHBoxLayout()
        content.setSpacing(14)
        charts = QFrame()
        charts.setObjectName("surface")
        charts_layout = QVBoxLayout(charts)
        charts_layout.setContentsMargins(12, 8, 12, 8)
        charts_layout.setSpacing(2)
        self.angle_plot = TrendPlot("Pendulum angle", "deg", "#277a68")
        self.position_plot = TrendPlot("Pivot position", "mm", "#d36b42")
        charts_layout.addWidget(self.angle_plot, 1)
        charts_layout.addWidget(self.position_plot, 1)
        content.addWidget(charts, 3)

        events_panel = QFrame()
        events_panel.setObjectName("surface")
        events_layout = QVBoxLayout(events_panel)
        events_layout.setContentsMargins(14, 12, 14, 12)
        events_layout.setSpacing(8)
        events_heading = QLabel("EVENTS & STATUS")
        events_heading.setObjectName("sectionTitle")
        events_hint = QLabel("Application messages and source state")
        events_hint.setObjectName("subtle")
        self.event_list = QListWidget()
        self.event_list.setObjectName("eventList")
        events_layout.addWidget(events_heading)
        events_layout.addWidget(events_hint)
        events_layout.addWidget(self.event_list, 1)
        content.addWidget(events_panel, 2)
        layout.addLayout(content, 1)

        self.controller.state.snapshot_changed.connect(self._update_snapshot)
        self.controller.state.telemetry_received.connect(self._append_sample)
        self.controller.state.event_added.connect(self._append_event)
        self._update_snapshot(self.controller.state.snapshot)

    @staticmethod
    def _state_pair(name: str, initial: str) -> tuple[QVBoxLayout, QLabel]:
        column = QVBoxLayout()
        label = QLabel(name)
        label.setObjectName("sectionTitle")
        value = QLabel(initial)
        value.setObjectName("stateValue")
        column.addWidget(label)
        column.addWidget(value)
        return column, value

    @staticmethod
    def _metric(name: str, unit: str) -> tuple[QFrame, QLabel]:
        surface = QFrame()
        surface.setObjectName("surface")
        column = QVBoxLayout(surface)
        column.setContentsMargins(14, 10, 14, 10)
        column.setSpacing(3)
        label = QLabel(name)
        label.setObjectName("sectionTitle")
        value = QLabel("--")
        value.setObjectName("metricValue")
        units = QLabel(unit)
        units.setObjectName("metricUnit")
        column.addWidget(label)
        column.addWidget(value)
        column.addWidget(units)
        return surface, value

    def _toggle_mock(self) -> None:
        if self.controller.mock_source.is_active:
            self.controller.stop_mock()
        else:
            self.controller.start_mock()

    def _update_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        self.connection_value[1].setText(snapshot.connection.value)
        self.motion_value[1].setText(snapshot.motion.value)
        self.recording_value[1].setText(snapshot.recording.value)
        self.mock_toggle_button.setText("Pause mock" if snapshot.mock_source_active else "Resume mock")
        self.mock_badge.setText(
            "MOCK SOURCE · LIVE" if snapshot.mock_source_active else "MOCK SOURCE · PAUSED"
        )
        self.samples_value[1].setText(f"{snapshot.samples_received:,}")
        if snapshot.latest_sample is not None:
            self.angle_value[1].setText(f"{snapshot.latest_sample.angle_deg:+.2f}")
            self.position_value[1].setText(f"{snapshot.latest_sample.position_mm:+.2f}")

    def _append_sample(self, sample: TelemetrySample) -> None:
        self._samples.append(sample)
        self.angle_plot.set_samples([(item.elapsed_s, item.angle_deg) for item in self._samples])
        self.position_plot.set_samples([(item.elapsed_s, item.position_mm) for item in self._samples])

    def _append_event(self, event: AppEvent) -> None:
        item_text = f"{event.timestamp.astimezone().strftime('%H:%M:%S')}  {event.message}"
        item = self.event_list.addItem(item_text)
        del item
        row = self.event_list.count() - 1
        color = {
            EventSeverity.INFO: "#277a68",
            EventSeverity.WARNING: "#b5682f",
            EventSeverity.ERROR: "#b34037",
        }[event.severity]
        self.event_list.item(row).setForeground(QColor(color))
        self.event_list.scrollToBottom()
        while self.event_list.count() > 200:
            self.event_list.takeItem(0)
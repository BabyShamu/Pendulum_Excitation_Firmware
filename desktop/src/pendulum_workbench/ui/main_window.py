from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
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
from pendulum_workbench.domain.models import (
    AppEvent,
    ApplicationSnapshot,
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    TelemetrySample,
)
from pendulum_workbench.ui.trend_plot import TrendPlot


class MainWindow(QMainWindow):
    def __init__(self, controller: WorkbenchController) -> None:
        super().__init__()
        self.controller = controller
        self._samples: deque[TelemetrySample] = deque(maxlen=180)
        self._last_displayed_source: DataSourceMode | None = None
        self._last_connection_state: ConnectionState | None = None
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
            QLabel#sourceBadge { color: #a8512c; background: #fff0e7; padding: 6px 9px; font-weight: 700; }
            QComboBox { background: #ffffff; border: 1px solid #cfdad4; padding: 7px; min-width: 130px; }
            QLabel#pathValue { color: #596c67; font-family: Consolas; font-size: 9px; }
            QPushButton { background: #247a68; color: white; border: 0; padding: 9px 14px; font-weight: 600; }
            QPushButton:hover { background: #1b6455; }
            QPushButton:disabled { background: #aebbb5; color: #f6f7f5; }
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
        eyebrow = QLabel("INSTRUMENTATION  /  MILESTONE 3")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("Pendulum Workbench")
        title.setObjectName("title")
        subtitle = QLabel("Live telemetry · monitoring only · no motion controls")
        subtitle.setObjectName("subtle")
        titles.addWidget(eyebrow)
        titles.addWidget(title)
        titles.addWidget(subtitle)
        header.addLayout(titles)
        header.addStretch(1)
        self.source_badge = QLabel("MOCK DATA · PAUSED")
        self.source_badge.setObjectName("sourceBadge")
        self.source_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_badge.setAccessibleName("Selected telemetry source and status")
        header.addWidget(self.source_badge, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        self.connection_panel = QFrame()
        self.connection_panel.setObjectName("surface")
        connection_layout = QHBoxLayout(self.connection_panel)
        connection_layout.setContentsMargins(14, 10, 14, 10)
        connection_layout.setSpacing(8)
        source_label = QLabel("DATA SOURCE")
        source_label.setObjectName("sectionTitle")
        connection_layout.addWidget(source_label)
        self.source_combo = QComboBox()
        self.source_combo.setObjectName("dataSourceCombo")
        self.source_combo.addItem("Mock telemetry", DataSourceMode.MOCK)
        self.source_combo.addItem("Real STM32", DataSourceMode.REAL)
        self.source_combo.currentIndexChanged.connect(self._source_selection_changed)
        connection_layout.addWidget(self.source_combo)
        self.port_combo = QComboBox()
        self.port_combo.setObjectName("serialPortCombo")
        self.port_combo.setMinimumWidth(220)
        connection_layout.addWidget(self.port_combo, 1)
        self.refresh_ports_button = QPushButton("Refresh ports")
        self.refresh_ports_button.setObjectName("refreshPortsButton")
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        connection_layout.addWidget(self.refresh_ports_button)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("connectSerialButton")
        self.connect_button.clicked.connect(self._connect_real)
        connection_layout.addWidget(self.connect_button)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setObjectName("disconnectSerialButton")
        self.disconnect_button.clicked.connect(self.controller.disconnect_real)
        connection_layout.addWidget(self.disconnect_button)
        layout.addWidget(self.connection_panel)

        self.capture_path_label = QLabel("Raw serial capture: not connected")
        self.capture_path_label.setObjectName("pathValue")
        self.capture_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.capture_path_label)

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
        layout.addWidget(state_panel)

        hardware_panel = QFrame()
        hardware_panel.setObjectName("surface")
        hardware_layout = QHBoxLayout(hardware_panel)
        hardware_layout.setContentsMargins(14, 9, 14, 9)
        hardware_layout.setSpacing(18)
        self.homed_value = self._state_pair("HOMED", "Unknown")
        self.upper_limit_value = self._state_pair("UPPER LIMIT", "Unknown")
        self.lower_limit_value = self._state_pair("LOWER LIMIT", "Unknown")
        self.active_mode_value = self._state_pair("ACTIVE MODE", "Unknown")
        self.fault_value = self._state_pair("FAULT", "Unknown")
        self.last_telemetry_value = self._state_pair("LAST VALID TELEMETRY", "No samples")
        for pair in (
            self.homed_value,
            self.upper_limit_value,
            self.lower_limit_value,
            self.active_mode_value,
            self.fault_value,
            self.last_telemetry_value,
        ):
            hardware_layout.addLayout(pair[0])
        layout.addWidget(hardware_panel)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.angle_value = self._metric("PENDULUM ANGLE", "deg")
        self.position_value = self._metric("PIVOT POSITION", "mm")
        self.samples_value = self._metric("SAMPLES RECEIVED", "samples")
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

        reserved_panel = QFrame()
        reserved_panel.setObjectName("surface")
        reserved_layout = QHBoxLayout(reserved_panel)
        reserved_layout.setContentsMargins(14, 9, 14, 9)
        reserved_title = QLabel("EXPERIMENT CONTROLS")
        reserved_title.setObjectName("sectionTitle")
        reserved_note = QLabel("Reserved for a later milestone · no hardware commands are available here")
        reserved_note.setObjectName("subtle")
        reserved_layout.addWidget(reserved_title)
        reserved_layout.addSpacing(12)
        reserved_layout.addWidget(reserved_note, 1)
        layout.addWidget(reserved_panel)

        self.controller.state.snapshot_changed.connect(self._update_snapshot)
        self.controller.state.telemetry_received.connect(self._append_sample)
        self.controller.state.event_added.connect(self._append_event)
        self._update_snapshot(self.controller.state.snapshot)
        self._refresh_ports()

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

    def _source_selection_changed(self, index: int) -> None:
        mode = self.source_combo.itemData(index)
        if mode is None:
            return
        try:
            self.controller.select_data_source(mode)
        except RuntimeError as error:
            self.controller.state.report_event(EventSeverity.WARNING, str(error))

    def _refresh_ports(self) -> None:
        selected_port = self.port_combo.currentData()
        self.port_combo.clear()
        try:
            ports = self.controller.available_ports()
        except Exception as error:
            ports = []
            self.controller.state.report_event(
                EventSeverity.ERROR,
                f"Serial port enumeration failed: {error}",
            )
        for port in ports:
            self.port_combo.addItem(f"{port.device}  ·  {port.description}", port.device)
        if not ports:
            self.port_combo.addItem("No serial ports detected", None)
        if selected_port is not None:
            selected_index = self.port_combo.findData(selected_port)
            if selected_index >= 0:
                self.port_combo.setCurrentIndex(selected_index)
        self._update_source_controls(self.controller.state.snapshot)

    def _connect_real(self) -> None:
        port = self.port_combo.currentData()
        if port is None:
            self.controller.state.report_event(
                EventSeverity.WARNING,
                "Select an available serial port before connecting.",
            )
            return
        self.controller.connect_real(port)

    def _update_source_controls(self, snapshot: ApplicationSnapshot) -> None:
        is_real = snapshot.data_source == DataSourceMode.REAL
        is_idle = snapshot.connection in (ConnectionState.DISCONNECTED, ConnectionState.ERROR)
        self.source_combo.setEnabled(is_idle)
        self.port_combo.setEnabled(is_real and is_idle)
        self.refresh_ports_button.setEnabled(is_real and is_idle)
        self.connect_button.setEnabled(
            is_real
            and is_idle
            and self.port_combo.currentData() is not None
        )
        self.disconnect_button.setEnabled(
            snapshot.connection in (ConnectionState.CONNECTING, ConnectionState.CONNECTED)
        )

    def _update_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        new_source = snapshot.data_source != self._last_displayed_source
        new_serial_session = (
            snapshot.connection == ConnectionState.CONNECTING
            and self._last_connection_state != ConnectionState.CONNECTING
        )
        if new_source or new_serial_session:
            self._samples.clear()
            self.angle_plot.set_samples([])
            self.position_plot.set_samples([])
            self.angle_value[1].setText("--")
            self.position_value[1].setText("--")
        self._last_displayed_source = snapshot.data_source
        self._last_connection_state = snapshot.connection

        mode_index = self.source_combo.findData(snapshot.data_source)
        if mode_index >= 0 and self.source_combo.currentIndex() != mode_index:
            self.source_combo.blockSignals(True)
            self.source_combo.setCurrentIndex(mode_index)
            self.source_combo.blockSignals(False)

        self.connection_value[1].setText(snapshot.connection.value)
        self.motion_value[1].setText(snapshot.motion.value)
        self.recording_value[1].setText(snapshot.recording.value)
        if snapshot.data_source == DataSourceMode.MOCK:
            self.source_badge.setText(
                "MOCK DATA · LIVE" if snapshot.mock_source_active else "MOCK DATA · PAUSED"
            )
            self.source_badge.setStyleSheet(
                "color: #a8512c; background: #fff0e7; padding: 6px 9px; font-weight: 700;"
            )
        else:
            self.source_badge.setText(f"REAL STM32 · {snapshot.connection.value.upper()}")
            badge_color = "#1b6455" if snapshot.connection == ConnectionState.CONNECTED else "#a8512c"
            badge_background = "#e5f4ed" if snapshot.connection == ConnectionState.CONNECTED else "#fff0e7"
            self.source_badge.setStyleSheet(
                f"color: {badge_color}; background: {badge_background}; padding: 6px 9px; font-weight: 700;"
            )
        self.samples_value[1].setText(f"{snapshot.samples_received:,}")
        if snapshot.latest_sample is not None:
            if snapshot.latest_sample.angle_deg is not None:
                self.angle_value[1].setText(f"{snapshot.latest_sample.angle_deg:+.2f}")
            if snapshot.latest_sample.position_mm is not None:
                self.position_value[1].setText(f"{snapshot.latest_sample.position_mm:+.2f}")

        hardware = snapshot.hardware_status
        self.homed_value[1].setText(self._bool_text(hardware.homed))
        self.upper_limit_value[1].setText(self._limit_text(hardware.upper_limit))
        self.lower_limit_value[1].setText(self._limit_text(hardware.lower_limit))
        self.active_mode_value[1].setText(hardware.active_motion_mode)
        self.fault_value[1].setText(
            "Unknown" if not hardware.fault_known else (hardware.fault or "No fault")
        )
        if snapshot.last_valid_telemetry_at is None:
            telemetry_text = "No valid samples"
        else:
            time_text = snapshot.last_valid_telemetry_at.astimezone().strftime("%H:%M:%S.%f")[:-3]
            telemetry_text = f"STALE · {time_text}" if snapshot.telemetry_stale else time_text
        self.last_telemetry_value[1].setText(telemetry_text)
        self.capture_path_label.setText(
            f"Raw serial capture: {snapshot.raw_capture_path or 'not connected'}"
        )
        self._update_source_controls(snapshot)

    @staticmethod
    def _bool_text(value: bool | None) -> str:
        return "Unknown" if value is None else ("Yes" if value else "No")

    @classmethod
    def _limit_text(cls, value: bool | None) -> str:
        return "Unknown" if value is None else ("Pressed" if value else "Clear")

    def _append_sample(self, sample: TelemetrySample) -> None:
        if sample.angle_deg is None or sample.position_mm is None:
            return
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
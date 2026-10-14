from collections import deque
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
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
    HomeStatus,
    MotionState,
    RecordingState,
    TelemetrySample,
)
from pendulum_workbench.experiments.repository import LoadedExperiment
from pendulum_workbench.ui.hardware_status_card import HardwareStatusCard
from pendulum_workbench.ui.home_status_control import HomeStatusControl
from pendulum_workbench.ui.status_indicator import IndicatorTone
from pendulum_workbench.ui.trend_plot import TrendPlot


class MonitorPage(QWidget):
    def __init__(self, controller: WorkbenchController) -> None:
        super().__init__()
        self.controller = controller
        self._samples: deque[TelemetrySample] = deque(maxlen=180)
        self._last_displayed_source: DataSourceMode | None = None
        self._last_connection_state: ConnectionState | None = None

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("monitorPageScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content_widget = QWidget()
        self.content_widget.setObjectName("monitorPageContent")
        self.content_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout = QVBoxLayout(self.content_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        outer_layout.addWidget(self.scroll_area)
        self.scroll_area.setWidget(self.content_widget)

        self.connection_panel = QFrame()
        self.connection_panel.setObjectName("surface")
        connection_layout = QHBoxLayout(self.connection_panel)
        connection_layout.setContentsMargins(14, 10, 14, 10)
        connection_layout.setSpacing(8)
        self.port_combo = QComboBox()
        self.port_combo.setObjectName("serialPortCombo")
        self.port_combo.setMinimumWidth(180)
        connection_layout.addWidget(self.port_combo, 1)
        self.refresh_ports_button = QPushButton("Refresh ports")
        self.refresh_ports_button.setObjectName("refreshPortsButton")
        self.refresh_ports_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.refresh_ports_button.clicked.connect(self._refresh_ports)
        connection_layout.addWidget(self.refresh_ports_button)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("connectSerialButton")
        self.connect_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.connect_button.clicked.connect(self._connect_real)
        connection_layout.addWidget(self.connect_button)
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setObjectName("disconnectSerialButton")
        self.disconnect_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton)
        )
        self.disconnect_button.clicked.connect(self.controller.disconnect_real)
        connection_layout.addWidget(self.disconnect_button)
        layout.addWidget(self.connection_panel)

        self.capture_path_label = QLabel("Raw serial capture: not connected")
        self.capture_path_label.setObjectName("pathValue")
        self.capture_path_label.setWordWrap(True)
        self.capture_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.capture_path_label)

        operational_row = QHBoxLayout()
        operational_row.setSpacing(12)
        hardware_panel = QFrame()
        self.hardware_panel = hardware_panel
        hardware_panel.setObjectName("surface")
        self._apply_card_depth(hardware_panel)
        hardware_panel.setMinimumWidth(380)
        hardware_panel.setMinimumHeight(232)
        hardware_layout = QVBoxLayout(hardware_panel)
        hardware_layout.setContentsMargins(12, 10, 12, 10)
        hardware_layout.setSpacing(8)
        hardware_heading = QLabel("HARDWARE STATUS")
        hardware_heading.setObjectName("sectionTitle")
        hardware_layout.addWidget(hardware_heading)
        self.home_status_control = HomeStatusControl()
        hardware_layout.addWidget(self.home_status_control)

        hardware_states = QHBoxLayout()
        hardware_states.setSpacing(8)
        self.upper_limit_value = HardwareStatusCard("Upper Limit")
        self.lower_limit_value = HardwareStatusCard("Lower Limit")
        self.fault_value = HardwareStatusCard("Fault")
        for status_card in (self.upper_limit_value, self.lower_limit_value, self.fault_value):
            hardware_states.addWidget(status_card, 1)
        hardware_layout.addLayout(hardware_states)

        hardware_text_row = QHBoxLayout()
        hardware_text_row.setSpacing(8)
        self.active_mode_value = self._info_card("ACTIVE MODE", "Unknown")
        self.last_telemetry_value = self._info_card("LAST TELEMETRY", "No valid samples")
        hardware_text_row.addWidget(self.active_mode_value[0], 1)
        hardware_text_row.addWidget(self.last_telemetry_value[0], 1)
        hardware_layout.addLayout(hardware_text_row)
        operational_row.addWidget(hardware_panel, 1)
        layout.addLayout(operational_row)

        metrics = QHBoxLayout()
        metrics.setSpacing(10)
        self.angle_value = self._metric("PENDULUM ANGLE", "deg")
        self.position_value = self._metric("PIVOT POSITION", "mm")
        self.samples_value = self._metric("SAMPLES RECEIVED", "samples")
        for metric in (self.angle_value, self.position_value, self.samples_value):
            self._apply_card_depth(metric[0])
            metrics.addWidget(metric[0], 1)
        layout.addLayout(metrics)

        content = QHBoxLayout()
        content.setSpacing(12)
        charts = QFrame()
        self.charts_panel = charts
        charts.setObjectName("surface")
        charts.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._apply_card_depth(charts)
        charts_layout = QVBoxLayout(charts)
        charts_layout.setContentsMargins(12, 8, 12, 8)
        charts_layout.setSpacing(2)
        self.angle_plot = TrendPlot("Pendulum angle", "deg", "#277a68")
        self.position_plot = TrendPlot("Pivot position", "mm", "#d36b42")
        self.angle_plot.setMinimumHeight(220)
        self.position_plot.setMinimumHeight(220)
        charts_layout.addWidget(self.angle_plot, 1)
        charts_layout.addWidget(self.position_plot, 1)
        content.addWidget(charts, 3)

        events_panel = QFrame()
        events_panel.setObjectName("surface")
        self._apply_card_depth(events_panel)
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
        self.controller.experiment_loaded.connect(self._show_saved_experiment)
        self._update_snapshot(self.controller.state.snapshot)
        self._refresh_ports()

    @staticmethod
    def _info_card(name: str, initial: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("hardwareInfoCard")
        card.setMinimumHeight(36)
        card.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(card)
        layout.setContentsMargins(9, 4, 9, 4)
        layout.setSpacing(6)
        label = QLabel(name)
        label.setObjectName("hardwareInfoKey")
        value = QLabel(initial)
        value.setObjectName("hardwareInfoValue")
        value.setWordWrap(False)
        value.setToolTip(initial)
        layout.addWidget(label)
        layout.addWidget(value, 1)
        return card, value

    @staticmethod
    def _metric(name: str, unit: str) -> tuple[QFrame, QLabel]:
        surface = QFrame()
        surface.setObjectName("surface")
        column = QVBoxLayout(surface)
        column.setContentsMargins(14, 8, 14, 8)
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

    @staticmethod
    def _apply_card_depth(card: QFrame) -> None:
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(36, 56, 47, 24))
        card.setGraphicsEffect(shadow)

    def refresh_ports(self) -> None:
        self._refresh_ports()

    def connect_from_menu(self) -> None:
        self._connect_real()

    def clear_events(self) -> None:
        self.event_list.clear()

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
        if self.controller.state.snapshot.data_source != DataSourceMode.REAL:
            self.controller.select_data_source(DataSourceMode.REAL)
        self.controller.connect_real(port)

    def _update_source_controls(self, snapshot: ApplicationSnapshot) -> None:
        is_idle = snapshot.connection in (ConnectionState.DISCONNECTED, ConnectionState.ERROR)
        self.port_combo.setEnabled(is_idle)
        self.refresh_ports_button.setEnabled(is_idle)
        self.connect_button.setEnabled(is_idle and self.port_combo.currentData() is not None)
        self.disconnect_button.setEnabled(
            snapshot.connection in (ConnectionState.CONNECTING, ConnectionState.CONNECTED)
            and not self.controller.maintenance.is_homing
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

        if snapshot.latest_sample is not None:
            if snapshot.latest_sample.angle_deg is not None:
                self.angle_value[1].setText(f"{snapshot.latest_sample.angle_deg:+.2f}")
            if snapshot.latest_sample.position_mm is not None:
                self.position_value[1].setText(f"{snapshot.latest_sample.position_mm:+.2f}")
        self.samples_value[1].setText(f"{snapshot.samples_received:,}")

        hardware = snapshot.hardware_status
        self.home_status_control.set_home_status(snapshot.home_status)
        self._set_limit_card(self.upper_limit_value, hardware.upper_limit)
        self._set_limit_card(self.lower_limit_value, hardware.lower_limit)
        self.active_mode_value[1].setText(hardware.active_motion_mode)
        self.active_mode_value[1].setToolTip(hardware.active_motion_mode)
        if not hardware.fault_known:
            fault_text, fault_tone = "Unknown", IndicatorTone.UNKNOWN
        elif hardware.fault:
            fault_text, fault_tone = hardware.fault, IndicatorTone.ERROR
        else:
            fault_text, fault_tone = "No fault", IndicatorTone.GOOD
        self.fault_value.set_state(fault_text, fault_tone)

        if snapshot.last_valid_telemetry_at is None:
            telemetry_text = "No valid samples"
            telemetry_color = "#596a66"
        else:
            time_text = snapshot.last_valid_telemetry_at.astimezone().strftime("%H:%M:%S.%f")[:-3]
            telemetry_text = f"STALE · {time_text}" if snapshot.telemetry_stale else time_text
            telemetry_color = "#8a4f0d" if snapshot.telemetry_stale else "#205f49"
        self.last_telemetry_value[1].setText(telemetry_text)
        self.last_telemetry_value[1].setToolTip(telemetry_text)
        self.last_telemetry_value[1].setStyleSheet(f"color: {telemetry_color}; font-weight: 600;")
        capture_path = snapshot.raw_capture_path or "not connected"
        self.capture_path_label.setText(f"Raw serial capture: {capture_path}")
        self.capture_path_label.setToolTip(capture_path)
        self._update_source_controls(snapshot)

    @staticmethod
    def _set_limit_card(card: HardwareStatusCard, active: bool | None) -> None:
        states = {
            True: ("Active", IndicatorTone.WARNING),
            False: ("Clear", IndicatorTone.NORMAL),
            None: ("Unknown", IndicatorTone.UNKNOWN),
        }
        card.set_state(*states[active])

    def _show_saved_experiment(self, experiment: LoadedExperiment) -> None:
        self._samples.clear()
        self._samples.extend(experiment.telemetry[-self._samples.maxlen:])
        self.angle_plot.set_samples(
            [(sample.elapsed_s, sample.angle_deg) for sample in self._samples
             if sample.angle_deg is not None]
        )
        self.position_plot.set_samples(
            [(sample.elapsed_s, sample.position_mm) for sample in self._samples
             if sample.position_mm is not None]
        )

    def _append_sample(self, sample: TelemetrySample) -> None:
        if sample.angle_deg is None or sample.position_mm is None:
            return
        self._samples.append(sample)
        self.angle_plot.set_samples(
            [(item.elapsed_s, item.angle_deg) for item in self._samples]
        )
        self.position_plot.set_samples(
            [(item.elapsed_s, item.position_mm) for item in self._samples]
        )

    def _append_event(self, event: AppEvent) -> None:
        item_text = f"{event.timestamp.astimezone().strftime('%H:%M:%S')}  {event.message}"
        self.event_list.addItem(item_text)
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
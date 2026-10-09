from collections import deque

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QGraphicsDropShadowEffect,
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
    MotionState,
    RecordingState,
    TelemetrySample,
)
from pendulum_workbench.domain.models import HomeStatus
from pendulum_workbench.ui.home_status_control import HomeStatusControl
from pendulum_workbench.ui.hardware_status_card import HardwareStatusCard
from pendulum_workbench.ui.status_indicator import IndicatorTone, StatusChip
from pendulum_workbench.ui.trend_plot import TrendPlot


class MainWindow(QMainWindow):
    def __init__(self, controller: WorkbenchController) -> None:
        super().__init__()
        self.controller = controller
        self._samples: deque[TelemetrySample] = deque(maxlen=180)
        self._last_displayed_source: DataSourceMode | None = None
        self._last_connection_state: ConnectionState | None = None
        self.setWindowTitle("Pendulum Workbench")
        self.resize(1220, 860)
        self.setMinimumSize(1000, 760)
        self.setStyleSheet(
            """
            QMainWindow { background: #e9eeeb; color: #24363a; }
            QWidget { background: transparent; color: #24363a; }
            QWidget#centralContent { background: #e9eeeb; }
            QLabel { background: transparent; }
            QLabel#eyebrow { color: #287b68; font-size: 10px; font-weight: 700; }
            QLabel#title { color: #203238; font-size: 27px; font-weight: 700; }
            QLabel#subtle { color: #71807d; font-size: 11px; }
            QFrame#surface { background: #ffffff; border: 1px solid #d8e1dc; border-radius: 11px; }
            QFrame#hardwareInfoCard { background: #f7f9f7; border: 1px solid #e0e7e2; border-radius: 6px; }
            QLabel#sectionTitle { color: #50615d; font-size: 10px; font-weight: 700; }
            QLabel#hardwareInfoKey { color: #6b7973; font-size: 8px; font-weight: 700; }
            QLabel#hardwareInfoValue { color: #33463d; font-size: 10px; font-weight: 600; }
            QLabel#metricValue { color: #203238; font-size: 23px; font-weight: 600; }
            QLabel#metricUnit { color: #76837f; font-size: 10px; }
            QLabel#sourceBadge { color: #a8512c; background: #fff0e7; padding: 6px 9px; font-weight: 700; }
            QComboBox { background: #ffffff; border: 1px solid #ccd8d1; border-radius: 7px; padding: 7px 9px; min-width: 130px; }
            QLabel#pathValue { color: #596c67; font-family: Consolas; font-size: 9px; }
            QPushButton { background: #f4f7f5; color: #28463b; border: 1px solid #cbd8d0; border-radius: 7px; padding: 8px 12px; font-weight: 600; }
            QPushButton:hover { background: #eaf2ed; border-color: #aac2b4; }
            QPushButton:pressed { background: #e1ebe4; }
            QPushButton:disabled { background: #f1f3f2; color: #8a9691; border-color: #dce2df; }
            QMenuBar { background: transparent; color: #344a41; }
            QMenuBar::item { background: transparent; padding: 5px 9px; border-radius: 5px; }
            QMenuBar::item:selected { background: #dfe9e3; }
            QMenu { background: #ffffff; border: 1px solid #d6e0da; border-radius: 7px; padding: 4px; }
            QMenu::item { padding: 6px 28px 6px 24px; border-radius: 4px; }
            QMenu::item:selected { background: #edf3ef; }
            QListWidget { background: #ffffff; border: 0; }
            """
        )

        root = QWidget()
        root.setObjectName("centralContent")
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
        self._apply_card_depth(self.connection_panel)
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
        self.capture_path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.capture_path_label)

        operational_row = QHBoxLayout()
        operational_row.setSpacing(14)

        hardware_panel = QFrame()
        self.hardware_panel = hardware_panel
        hardware_panel.setObjectName("surface")
        self._apply_card_depth(hardware_panel)
        hardware_panel.setMinimumWidth(420)
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
        hardware_layout.addSpacing(8)

        hardware_text_row = QHBoxLayout()
        hardware_text_row.setSpacing(8)
        self.active_mode_value = self._info_card("ACTIVE MODE", "Unknown")
        self.last_telemetry_value = self._info_card("LAST TELEMETRY", "No samples")
        hardware_text_row.addWidget(self.active_mode_value[0], 1)
        hardware_text_row.addWidget(self.last_telemetry_value[0], 1)
        hardware_layout.addLayout(hardware_text_row)
        operational_row.addWidget(hardware_panel, 3)

        experiment_panel = QFrame()
        experiment_panel.setObjectName("surface")
        self._apply_card_depth(experiment_panel)
        experiment_panel.setMinimumHeight(232)
        experiment_layout = QVBoxLayout(experiment_panel)
        experiment_layout.setContentsMargins(20, 16, 20, 16)
        experiment_layout.setSpacing(14)
        experiment_heading = QLabel("EXPERIMENT CONTROLS")
        experiment_heading.setObjectName("sectionTitle")
        experiment_layout.addWidget(experiment_heading)

        experiment_summary = QLabel("No experiment selected")
        experiment_summary.setObjectName("subtle")
        experiment_layout.addWidget(experiment_summary)

        placeholders = QHBoxLayout()
        placeholders.setSpacing(30)
        for heading, detail in (
            ("EXPERIMENT", "Selection unavailable"),
            ("EXCITATION", "Not configured"),
            ("EXECUTION", "Unavailable in Milestone 3"),
        ):
            group = QVBoxLayout()
            group.setSpacing(7)
            label = QLabel(heading)
            label.setObjectName("sectionTitle")
            value = QLabel(detail)
            value.setObjectName("subtle")
            group.addWidget(label)
            group.addWidget(value)
            group.addStretch(1)
            placeholders.addLayout(group, 1)
        experiment_layout.addLayout(placeholders, 1)
        operational_row.addWidget(experiment_panel, 4)
        layout.addLayout(operational_row)

        metrics = QHBoxLayout()
        metrics.setSpacing(12)
        self.angle_value = self._metric("PENDULUM ANGLE", "deg")
        self.position_value = self._metric("PIVOT POSITION", "mm")
        self.samples_value = self._metric("SAMPLES RECEIVED", "samples")
        for metric in (self.angle_value, self.position_value, self.samples_value):
            self._apply_card_depth(metric[0])
            metrics.addWidget(metric[0], 1)
        layout.addLayout(metrics)

        content = QHBoxLayout()
        content.setSpacing(14)
        charts = QFrame()
        charts.setObjectName("surface")
        self._apply_card_depth(charts)
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

        state_panel = QFrame()
        state_panel.setObjectName("surface")
        self._apply_card_depth(state_panel)
        state_layout = QHBoxLayout(state_panel)
        state_layout.setContentsMargins(16, 8, 16, 8)
        state_layout.setSpacing(28)
        self.connection_value = StatusChip("Device", "Disconnected", IndicatorTone.NORMAL)
        self.motion_value = StatusChip("Motion")
        self.recording_value = StatusChip("Recording", "Off", IndicatorTone.NORMAL)
        state_layout.addWidget(self.connection_value)
        state_layout.addWidget(self.motion_value)
        state_layout.addWidget(self.recording_value)
        state_layout.addStretch(1)
        layout.addWidget(state_panel)

        self.controller.state.snapshot_changed.connect(self._update_snapshot)
        self.controller.state.telemetry_received.connect(self._append_sample)
        self.controller.state.event_added.connect(self._append_event)
        self._update_snapshot(self.controller.state.snapshot)
        self._refresh_ports()
        self._create_menu_bar()

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
        layout.addWidget(label)
        layout.addWidget(value, 1)
        return card, value

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

    @staticmethod
    def _apply_card_depth(card: QFrame) -> None:
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(36, 56, 47, 24))
        card.setGraphicsEffect(shadow)

    def _create_menu_bar(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        self.open_experiment_action = QAction("Open Experiment", self)
        self.open_experiment_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        )
        self.open_experiment_action.triggered.connect(self._open_experiment)
        file_menu.addAction(self.open_experiment_action)
        file_menu.addSeparator()
        self.exit_action = QAction("Exit", self)
        self.exit_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCloseButton)
        )
        self.exit_action.triggered.connect(self.close)
        file_menu.addAction(self.exit_action)

        device_menu = self.menuBar().addMenu("Device")
        self.refresh_ports_action = QAction("Refresh Ports", self)
        self.refresh_ports_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.refresh_ports_action.triggered.connect(self._refresh_ports)
        device_menu.addAction(self.refresh_ports_action)
        self.connect_action = QAction("Connect", self)
        self.connect_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.connect_action.triggered.connect(self._connect_from_menu)
        device_menu.addAction(self.connect_action)
        self.disconnect_action = QAction("Disconnect", self)
        self.disconnect_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogCancelButton)
        )
        self.disconnect_action.triggered.connect(self.controller.disconnect_real)
        device_menu.addAction(self.disconnect_action)

        view_menu = self.menuBar().addMenu("View")
        self.clear_events_action = QAction("Clear Events", self)
        self.clear_events_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton)
        )
        self.clear_events_action.triggered.connect(self.event_list.clear)
        view_menu.addAction(self.clear_events_action)

        help_menu = self.menuBar().addMenu("Help")
        self.about_action = QAction("About", self)
        self.about_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        )
        self.about_action.triggered.connect(self._show_about)
        help_menu.addAction(self.about_action)

    def _open_experiment(self) -> None:
        self.controller.state.report_event(
            EventSeverity.INFO,
            "Opening saved experiments is not available in Milestone 3.",
        )

    def _connect_from_menu(self) -> None:
        if self.controller.state.snapshot.data_source != DataSourceMode.REAL:
            real_index = self.source_combo.findData(DataSourceMode.REAL)
            if real_index >= 0:
                self.source_combo.setCurrentIndex(real_index)
        self._connect_real()

    def _show_about(self) -> None:
        QMessageBox.about(
            self,
            "About Pendulum Workbench",
            "Pendulum Workbench\n\n"
            "Milestone 3 provides live telemetry and read-only hardware status. "
            "Experiment motion commands are not available.",
        )

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

        connection_indicators = {
            ConnectionState.DISCONNECTED: IndicatorTone.NORMAL,
            ConnectionState.CONNECTING: IndicatorTone.WARNING,
            ConnectionState.CONNECTED: IndicatorTone.GOOD,
            ConnectionState.DISCONNECTING: IndicatorTone.WARNING,
            ConnectionState.ERROR: IndicatorTone.ERROR,
        }
        self.connection_value.set_status(
            snapshot.connection.value,
            connection_indicators[snapshot.connection],
        )
        motion_text = snapshot.motion.value
        motion_tone = IndicatorTone.UNKNOWN
        if snapshot.motion in (MotionState.READY, MotionState.IDLE):
            motion_tone = IndicatorTone.NORMAL
        elif snapshot.motion == MotionState.RUNNING:
            motion_tone = IndicatorTone.GOOD
        elif snapshot.motion == MotionState.HOMING:
            motion_tone = IndicatorTone.WARNING
        elif snapshot.motion == MotionState.STOPPING:
            motion_tone = IndicatorTone.WARNING
        elif snapshot.motion == MotionState.FAULT:
            motion_tone = IndicatorTone.ERROR
        self.motion_value.set_status(motion_text, motion_tone)
        recording_indicators = {
            RecordingState.OFF: ("Off", IndicatorTone.NORMAL),
            RecordingState.STARTING: ("Starting", IndicatorTone.WARNING),
            RecordingState.RECORDING: ("Recording", IndicatorTone.GOOD),
            RecordingState.STOPPING: ("Stopping", IndicatorTone.WARNING),
            RecordingState.SAVED: ("Saved", IndicatorTone.GOOD),
            RecordingState.INTERRUPTED: ("Interrupted", IndicatorTone.WARNING),
            RecordingState.ERROR: ("Error", IndicatorTone.ERROR),
        }
        self.recording_value.set_status(*recording_indicators[snapshot.recording])
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
        self.home_status_control.set_home_status(snapshot.home_status)
        self._set_limit_card(self.upper_limit_value, hardware.upper_limit)
        self._set_limit_card(self.lower_limit_value, hardware.lower_limit)

        mode = hardware.active_motion_mode
        self.active_mode_value[1].setText(mode)

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
        self.last_telemetry_value[1].setStyleSheet(f"color: {telemetry_color}; font-weight: 600;")
        self.capture_path_label.setText(
            f"Raw serial capture: {snapshot.raw_capture_path or 'not connected'}"
        )
        self._update_source_controls(snapshot)

    @staticmethod
    def _set_limit_card(card: HardwareStatusCard, active: bool | None) -> None:
        states = {
            True: ("Active", IndicatorTone.WARNING),
            False: ("Clear", IndicatorTone.NORMAL),
            None: ("Unknown", IndicatorTone.UNKNOWN),
        }
        card.set_state(*states[active])

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
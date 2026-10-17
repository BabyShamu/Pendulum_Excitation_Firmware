from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import (
    ApplicationSnapshot,
    ConnectionState,
    DataSourceMode,
    MotionState,
    RecordingState,
)
from pendulum_workbench.experiments.repository import LoadedExperiment
from pendulum_workbench.ui.pages.experiment_page import ExperimentPage
from pendulum_workbench.ui.pages.live_page import MonitorPage
from pendulum_workbench.ui.pages.maintenance_page import MaintenancePage
from pendulum_workbench.ui.status_indicator import IndicatorTone, StatusChip


class MainWindow(QMainWindow):
    def __init__(self, controller: WorkbenchController) -> None:
        super().__init__()
        self.controller = controller
        self._saved_experiment_name: str | None = None
        self.setWindowTitle("Pendulum Workbench")
        self.resize(1220, 860)
        self.setMinimumSize(900, 640)
        self.setStyleSheet(
            """
            QMainWindow { background: #e9eeeb; color: #24363a; }
            QWidget { background: transparent; color: #24363a; }
            QWidget#centralContent { background: #e9eeeb; }
            QLabel { background: transparent; }
            QLabel#eyebrow { color: #287b68; font-size: 10px; font-weight: 700; }
            QLabel#title { color: #203238; font-size: 25px; font-weight: 700; }
            QLabel#subtle { color: #71807d; font-size: 11px; }
            QLabel#sectionTitle { color: #50615d; font-size: 10px; font-weight: 700; }
            QFrame#hardwareInfoCard { background: #f7f9f7; border: 1px solid #e0e7e2; border-radius: 6px; }
            QLabel#hardwareInfoKey { color: #6b7973; font-size: 8px; font-weight: 700; }
            QLabel#hardwareInfoValue { color: #33463d; font-size: 10px; font-weight: 600; }
            QLabel#metricValue { color: #203238; font-size: 23px; font-weight: 600; }
            QLabel#metricUnit { color: #76837f; font-size: 10px; }
            QLabel#pathValue, QLabel#experimentPathValue { color: #596c67; font-family: Consolas; font-size: 9px; }
            QLabel#summaryKey { color: #71807d; font-size: 9px; font-weight: 700; }
            QLabel#summaryValue { color: #33463d; font-size: 10px; }
            QLabel#summaryPath { color: #596c67; font-family: Consolas; font-size: 9px; }
            QLabel#sourceBadge { color: #a8512c; background: #fff0e7; padding: 6px 9px; font-weight: 700; }
            QComboBox { background: #ffffff; border: 1px solid #ccd8d1; border-radius: 7px; padding: 7px 9px; min-width: 110px; }
            QLineEdit { background: #ffffff; border: 1px solid #d5dfd9; border-radius: 6px; padding: 7px 9px; }
            QLineEdit:focus { border-color: #8ab29a; }
            QPushButton { background: #f4f7f5; color: #28463b; border: 1px solid #cbd8d0; border-radius: 7px; padding: 8px 11px; font-weight: 600; }
            QPushButton:hover { background: #eaf2ed; border-color: #aac2b4; }
            QPushButton:pressed { background: #e1ebe4; }
            QPushButton:disabled { background: #f1f3f2; color: #8a9691; border-color: #dce2df; }
            QFrame#surface { background: #ffffff; border: 1px solid #d8e1dc; border-radius: 9px; }
            QFrame#navRail { background: #f3f6f4; border: 1px solid #d8e1dc; border-radius: 8px; }
            QPushButton#workspaceNavigationButton { text-align: left; background: transparent; border: 0; border-left: 3px solid transparent; border-radius: 5px; padding: 10px 9px; color: #50615d; }
            QPushButton#workspaceNavigationButton:hover { background: #e7eeea; }
            QPushButton#workspaceNavigationButton[selected="true"] { background: #dcebe2; border-left-color: #287b68; color: #205f49; font-weight: 700; }
            QPushButton#manualMoveButton { min-height: 72px; font-size: 14px; font-weight: 700; background: #edf3ef; }
            QPushButton#manualMoveButton[manualActive="true"] { background: #277a68; color: #ffffff; border-color: #205f49; }
            QLabel#manualMotionState { color: #50615d; font-size: 13px; font-weight: 700; }
            QMenuBar { background: #f3f6f4; color: #24363a; border-bottom: 1px solid #d8e1dc; }
            QMenuBar::item { background: transparent; padding: 5px 9px; border-radius: 5px; }
            QMenuBar::item:selected { background: #dfe9e3; color: #203238; }
            QMenu { background: #ffffff; color: #24363a; border: 1px solid #d6e0da; border-radius: 7px; padding: 4px; }
            QMenu::item { padding: 6px 28px 6px 24px; border-radius: 4px; }
            QMenu::item:selected { background: #edf3ef; color: #203238; }
            QListWidget { background: #ffffff; border: 0; }
            """
        )

        root = QWidget()
        root.setObjectName("centralContent")
        layout = QVBoxLayout(root)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(12)
        self.setCentralWidget(root)

        header = QHBoxLayout()
        title_group = QVBoxLayout()
        eyebrow = QLabel("EXPERIMENT WORKBENCH")
        eyebrow.setObjectName("eyebrow")
        title = QLabel("Pendulum Workbench")
        title.setObjectName("title")
        subtitle = QLabel("Hardware monitor and experiment records")
        subtitle.setObjectName("subtle")
        title_group.addWidget(eyebrow)
        title_group.addWidget(title)
        title_group.addWidget(subtitle)
        header.addLayout(title_group)
        header.addStretch(1)
        self.source_badge = QLabel()
        self.source_badge.setObjectName("sourceBadge")
        self.source_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.source_badge.setAccessibleName("Selected telemetry source and device state")
        header.addWidget(self.source_badge, alignment=Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        workspace = QHBoxLayout()
        workspace.setSpacing(12)
        navigation = QFrame()
        navigation.setObjectName("navRail")
        navigation.setFixedWidth(148)
        self.navigation_rail = navigation
        nav_layout = QVBoxLayout(navigation)
        self.navigation_layout = nav_layout
        nav_layout.setContentsMargins(8, 12, 8, 10)
        nav_layout.setSpacing(5)
        nav_heading = QLabel("WORKSPACE")
        nav_heading.setObjectName("sectionTitle")
        nav_heading.setContentsMargins(8, 2, 0, 8)
        nav_layout.addWidget(nav_heading)
        self.navigation_group = QButtonGroup(self)
        self.navigation_group.setExclusive(True)
        self.monitor_navigation_button = self._navigation_button("Monitor", "monitorNavigationButton", 0)
        self.live_navigation_button = self.monitor_navigation_button
        self.experiment_navigation_button = self._navigation_button(
            "Experiment", "experimentNavigationButton", 1
        )
        for index, button in enumerate(
            (self.monitor_navigation_button, self.experiment_navigation_button)
        ):
            self.navigation_group.addButton(button, index)
            button.clicked.connect(lambda _checked=False, page=index: self._show_page(page))
            nav_layout.addWidget(button)
        nav_layout.addStretch(1)
        separator = QFrame()
        separator.setFrameShape(QFrame.Shape.HLine)
        separator.setStyleSheet("color: #d8e1dc;")
        nav_layout.addWidget(separator)
        self.maintenance_navigation_button = self._navigation_button(
            "Maintenance", "maintenanceNavigationButton", 2
        )
        self.navigation_group.addButton(self.maintenance_navigation_button, 2)
        self.maintenance_navigation_button.clicked.connect(
            lambda _checked=False: self._show_page(2)
        )
        nav_layout.addWidget(self.maintenance_navigation_button)
        workspace.addWidget(navigation)

        self.page_stack = QStackedWidget()
        self.page_stack.setObjectName("pageStack")
        self.page_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.monitor_page = MonitorPage(controller)
        self.live_page = self.monitor_page
        self.experiment_page = ExperimentPage(controller)
        self.maintenance_page = MaintenancePage(controller)
        self.page_stack.addWidget(self.monitor_page)
        self.page_stack.addWidget(self.experiment_page)
        self.page_stack.addWidget(self.maintenance_page)
        workspace.addWidget(self.page_stack, 1)
        layout.addLayout(workspace, 1)

        status_panel = QFrame()
        status_panel.setObjectName("surface")
        self._apply_card_depth(status_panel)
        status_layout = QHBoxLayout(status_panel)
        status_layout.setContentsMargins(14, 7, 14, 7)
        status_layout.setSpacing(24)
        self.connection_value = StatusChip("Device", "Disconnected", IndicatorTone.NORMAL)
        self.motion_value = StatusChip("Motion")
        self.recording_value = StatusChip("Recording", "Off", IndicatorTone.NORMAL)
        status_layout.addWidget(self.connection_value)
        status_layout.addWidget(self.motion_value)
        status_layout.addWidget(self.recording_value)
        status_layout.addStretch(1)
        layout.addWidget(status_panel)

        self.controller.state.snapshot_changed.connect(self._update_global_status)
        self.controller.experiment_loaded.connect(self._on_experiment_loaded)
        self._update_global_status(self.controller.state.snapshot)
        self._show_page(0)
        self._create_menu_bar()

    @staticmethod
    def _navigation_button(text: str, object_name: str, index: int) -> QPushButton:
        button = QPushButton(text)
        button.setObjectName("workspaceNavigationButton")
        button.setProperty("navigationId", object_name)
        button.setProperty("pageIndex", index)
        button.setCheckable(True)
        button.setProperty("selected", False)
        button.setAccessibleName(f"Show {text} page")
        button.setAccessibleDescription(f"Navigate to the {text.lower()} workspace page")
        return button

    @staticmethod
    def _apply_card_depth(card: QFrame) -> None:
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(36, 56, 47, 24))
        card.setGraphicsEffect(shadow)

    def _show_page(self, index: int) -> None:
        if index < 0 or index >= self.page_stack.count():
            return
        if (
            self.page_stack.currentWidget() is self.maintenance_page
            and index != self.page_stack.indexOf(self.maintenance_page)
        ):
            self.controller.maintenance.stop_manual_motion("maintenance_page_left")
        self.page_stack.setCurrentIndex(index)
        for page_index in range(self.page_stack.count()):
            page_button = self.navigation_group.button(page_index)
            if page_button is None:
                continue
            selected = page_index == index
            page_button.setChecked(selected)
            page_button.setProperty("selected", selected)
            page_button.style().unpolish(page_button)
            page_button.style().polish(page_button)

    def _create_menu_bar(self) -> None:
        file_menu = self.menuBar().addMenu("File")
        self.open_experiment_action = QAction("Open Experiment", self)
        self.open_experiment_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileIcon)
        )
        self.open_experiment_action.triggered.connect(self.experiment_page.open_experiment_dialog)
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
        self.refresh_ports_action.triggered.connect(self.live_page.refresh_ports)
        device_menu.addAction(self.refresh_ports_action)
        self.connect_action = QAction("Connect", self)
        self.connect_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton)
        )
        self.connect_action.triggered.connect(self.live_page.connect_from_menu)
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
        self.clear_events_action.triggered.connect(self.live_page.clear_events)
        view_menu.addAction(self.clear_events_action)

        help_menu = self.menuBar().addMenu("Help")
        self.about_action = QAction("About", self)
        self.about_action.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_MessageBoxInformation)
        )
        self.about_action.triggered.connect(self._show_about)
        help_menu.addAction(self.about_action)
        self._update_global_status(self.controller.state.snapshot)

    def _update_global_status(self, snapshot: ApplicationSnapshot) -> None:
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
        motion_tones = {
            MotionState.UNKNOWN: IndicatorTone.UNKNOWN,
            MotionState.IDLE: IndicatorTone.NORMAL,
            MotionState.READY: IndicatorTone.NORMAL,
            MotionState.RUNNING: IndicatorTone.GOOD,
            MotionState.HOMING: IndicatorTone.WARNING,
            MotionState.STOPPING: IndicatorTone.WARNING,
            MotionState.FAULT: IndicatorTone.ERROR,
        }
        self.motion_value.set_status(snapshot.motion.value, motion_tones[snapshot.motion])
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
        if hasattr(self, "disconnect_action"):
            self.disconnect_action.setEnabled(
                snapshot.connection in (ConnectionState.CONNECTING, ConnectionState.CONNECTED)
                and not self.controller.maintenance.is_homing
            )
        self._update_source_badge(snapshot)

    def _update_source_badge(self, snapshot: ApplicationSnapshot) -> None:
        if snapshot.data_source == DataSourceMode.MOCK:
            text = "MOCK DATA · LIVE" if snapshot.mock_source_active else "MOCK DATA · PAUSED"
            foreground, background = "#a8512c", "#fff0e7"
        elif snapshot.data_source == DataSourceMode.SAVED:
            text = f"SAVED EXPERIMENT · {self._saved_experiment_name or 'Saved data'}"
            foreground, background = "#344f67", "#eaf1f7"
        else:
            text = f"REAL STM32 · {snapshot.connection.value.upper()}"
            if snapshot.connection == ConnectionState.CONNECTED:
                foreground, background = "#1b6455", "#e5f4ed"
            else:
                foreground, background = "#a8512c", "#fff0e7"
        self.source_badge.setText(text)
        self.source_badge.setStyleSheet(
            f"color: {foreground}; background: {background}; padding: 6px 9px; font-weight: 700;"
        )

    def _on_experiment_loaded(self, experiment: LoadedExperiment) -> None:
        self._saved_experiment_name = experiment.manifest.get("name") or experiment.experiment_id
        self._update_source_badge(self.controller.state.snapshot)

    def _show_about(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("About Pendulum Workbench")
        dialog.setMinimumWidth(480)
        dialog.setStyleSheet(
            "QDialog { background: #f7f9f7; color: #24363a; }"
            "QLabel { color: #24363a; background: transparent; }"
        )
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(12)
        image_path = Path(__file__).resolve().parents[1] / "assets" / "pendulum_cad.png"
        if image_path.is_file():
            from PySide6.QtGui import QPixmap
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image_label.setPixmap(
                QPixmap(str(image_path)).scaled(
                    300, 330,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
            layout.addWidget(image_label)
        heading = QLabel("Pendulum Workbench")
        heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        heading.setStyleSheet("font-size: 20px; font-weight: 700;")
        layout.addWidget(heading)
        description = QLabel("Experimental Control & Data Acquisition")
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(dialog.accept)
        layout.addWidget(ok_button, alignment=Qt.AlignmentFlag.AlignRight)
        dialog.exec()

    def closeEvent(self, event) -> None:
        if self.controller.maintenance.is_manual_motion_active:
            self.controller.maintenance.stop_manual_motion("application_shutdown")
            QMessageBox.warning(
                self,
                "Manual Motion Stopping",
                "Wait for manual pivot motion to stop before closing the workbench.",
            )
            event.ignore()
            return
        if self.controller.maintenance.is_homing:
            QMessageBox.warning(
                self,
                "Homing in Progress",
                "Wait for the firmware homing cycle to finish before closing the workbench.",
            )
            event.ignore()
            return
        super().closeEvent(event)
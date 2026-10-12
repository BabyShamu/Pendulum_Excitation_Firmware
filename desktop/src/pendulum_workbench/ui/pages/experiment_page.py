from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import (
    ApplicationSnapshot,
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    MotionState,
    RecordingState,
)
from pendulum_workbench.experiments.repository import LoadedExperiment


class ExperimentPage(QWidget):
    def __init__(self, controller: WorkbenchController) -> None:
        super().__init__()
        self.controller = controller
        self._recording_name = ""
        self._recording_notes = ""
        self._experiment_directory: Path | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        recording_panel = QFrame()
        recording_panel.setObjectName("surface")
        self._apply_card_depth(recording_panel)
        recording_layout = QVBoxLayout(recording_panel)
        recording_layout.setContentsMargins(18, 14, 18, 14)
        recording_layout.setSpacing(10)
        recording_heading = QLabel("NEW / ACTIVE RECORDING")
        recording_heading.setObjectName("sectionTitle")
        recording_layout.addWidget(recording_heading)
        self.readiness_indicator = QLabel("CHECK REQUIRED")
        self.readiness_indicator.setObjectName("experimentReadinessIndicator")
        recording_layout.addWidget(self.readiness_indicator)

        fields = QHBoxLayout()
        fields.setSpacing(12)
        name_group = QVBoxLayout()
        name_label = QLabel("EXPERIMENT NAME")
        name_label.setObjectName("sectionTitle")
        self.experiment_name_input = QLineEdit()
        self.experiment_name_input.setObjectName("experimentNameInput")
        self.experiment_name_input.setPlaceholderText("Optional run name")
        name_group.addWidget(name_label)
        name_group.addWidget(self.experiment_name_input)
        notes_group = QVBoxLayout()
        notes_label = QLabel("NOTES")
        notes_label.setObjectName("sectionTitle")
        self.experiment_notes_input = QLineEdit()
        self.experiment_notes_input.setObjectName("experimentNotesInput")
        self.experiment_notes_input.setPlaceholderText("Operator notes (optional)")
        notes_group.addWidget(notes_label)
        notes_group.addWidget(self.experiment_notes_input)
        fields.addLayout(name_group, 1)
        fields.addLayout(notes_group, 2)
        recording_layout.addLayout(fields)

        controls = QHBoxLayout()
        controls.setSpacing(8)
        self.start_recording_button = QPushButton("Start Recording")
        self.start_recording_button.setObjectName("startRecordingButton")
        self.start_recording_button.clicked.connect(self._start_recording)
        controls.addWidget(self.start_recording_button)
        self.stop_recording_button = QPushButton("Stop Recording")
        self.stop_recording_button.setObjectName("stopRecordingButton")
        self.stop_recording_button.clicked.connect(self._stop_recording)
        controls.addWidget(self.stop_recording_button)
        self.recording_state_value = QLabel("Recording is idle")
        self.recording_state_value.setObjectName("subtle")
        self.recording_state_value.setWordWrap(True)
        controls.addWidget(self.recording_state_value, 1)
        recording_layout.addLayout(controls)

        self.current_experiment_path = QLabel("No active experiment")
        self.current_experiment_path.setObjectName("experimentPathValue")
        self.current_experiment_path.setWordWrap(True)
        self.current_experiment_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        recording_layout.addWidget(self.current_experiment_path)
        layout.addWidget(recording_panel)

        saved_panel = QFrame()
        saved_panel.setObjectName("surface")
        self._apply_card_depth(saved_panel)
        saved_layout = QVBoxLayout(saved_panel)
        saved_layout.setContentsMargins(18, 14, 18, 14)
        saved_layout.setSpacing(10)
        saved_header = QHBoxLayout()
        saved_heading = QLabel("SAVED EXPERIMENT")
        saved_heading.setObjectName("sectionTitle")
        saved_header.addWidget(saved_heading)
        saved_header.addStretch(1)
        self.open_experiment_button = QPushButton("Open Experiment")
        self.open_experiment_button.setObjectName("openExperimentButton")
        self.open_experiment_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogStart)
        )
        self.open_experiment_button.clicked.connect(self.open_experiment_dialog)
        saved_header.addWidget(self.open_experiment_button)
        self.open_recording_folder_button = QPushButton("Open Recording Folder")
        self.open_recording_folder_button.setObjectName("openRecordingFolderButton")
        self.open_recording_folder_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon)
        )
        self.open_recording_folder_button.setEnabled(False)
        self.open_recording_folder_button.clicked.connect(self._open_recording_folder)
        saved_header.addWidget(self.open_recording_folder_button)
        saved_layout.addLayout(saved_header)

        self.saved_experiment_summary = QWidget()
        self.saved_experiment_summary.setObjectName("savedExperimentSummary")
        summary = QGridLayout(self.saved_experiment_summary)
        summary.setContentsMargins(0, 0, 0, 0)
        summary.setHorizontalSpacing(10)
        summary.setVerticalSpacing(8)
        self.saved_name_value = self._summary_value("savedExperimentNameValue")
        self.saved_notes_value = self._summary_value("savedExperimentNotesValue")
        self.saved_id_value = self._summary_value("savedExperimentIdValue", technical=True)
        self.saved_path_value = self._summary_value("savedExperimentPathValue", technical=True)
        self.saved_status_value = self._summary_value("savedExperimentStatusValue")
        self.saved_sample_count_value = self._summary_value("savedExperimentSampleCountValue")
        self.saved_data_source_value = self._summary_value("savedExperimentDataSourceValue")
        summary.addWidget(self._summary_key("NAME"), 0, 0)
        summary.addWidget(self.saved_name_value, 0, 1)
        summary.addWidget(self._summary_key("NOTES"), 0, 2)
        summary.addWidget(self.saved_notes_value, 0, 3)
        summary.addWidget(self._summary_key("EXPERIMENT UUID"), 1, 0)
        summary.addWidget(self.saved_id_value, 1, 1, 1, 3)
        summary.addWidget(self._summary_key("FOLDER"), 2, 0)
        summary.addWidget(self.saved_path_value, 2, 1, 1, 3)
        summary.addWidget(self._summary_key("COMPLETION"), 3, 0)
        summary.addWidget(self.saved_status_value, 3, 1)
        summary.addWidget(self._summary_key("SAMPLES"), 3, 2)
        summary.addWidget(self.saved_sample_count_value, 3, 3)
        summary.addWidget(self._summary_key("DATA SOURCE"), 4, 0)
        summary.addWidget(self.saved_data_source_value, 4, 1, 1, 3)
        summary.setColumnStretch(1, 1)
        summary.setColumnStretch(3, 2)
        self.saved_experiment_summary.setVisible(False)
        saved_layout.addWidget(self.saved_experiment_summary)
        layout.addWidget(saved_panel)
        layout.addStretch(1)

        self.controller.state.snapshot_changed.connect(self._update_snapshot)
        recorder = self.controller.experiment_recorder
        recorder.recording_requested.connect(self._on_recording_requested)
        recorder.recording_started.connect(self._on_recording_started)
        recorder.recording_finished.connect(self._on_recording_finished)
        recorder.recording_failed.connect(self._on_recording_failed)
        self.controller.experiment_loaded.connect(self._show_saved_experiment)
        self.controller.maintenance.snapshot_changed.connect(self._update_readiness)
        self._update_snapshot(self.controller.state.snapshot)
        self._update_readiness(self.controller.maintenance.snapshot)

    @staticmethod
    def _apply_card_depth(card: QFrame) -> None:
        shadow = QGraphicsDropShadowEffect(card)
        shadow.setBlurRadius(16)
        shadow.setOffset(0, 3)
        shadow.setColor(QColor(36, 56, 47, 24))
        card.setGraphicsEffect(shadow)

    @staticmethod
    def _summary_key(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("summaryKey")
        return label

    @staticmethod
    def _summary_value(object_name: str, *, technical: bool = False) -> QLabel:
        label = QLabel("-")
        label.setObjectName(object_name)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        if technical:
            label.setStyleSheet("color: #596c67; font-family: Consolas; font-size: 9px;")
        else:
            label.setObjectName(object_name)
        return label

    def open_experiment_dialog(self) -> None:
        source, _ = QFileDialog.getOpenFileName(
            self,
            "Open Experiment Manifest",
            str(self.controller.experiment_recorder.repository.root),
            "Experiment manifest (manifest.json)",
        )
        if not source:
            return
        try:
            self.controller.open_experiment(source)
        except (OSError, ValueError, RuntimeError, KeyError) as error:
            self.controller.state.report_event(EventSeverity.ERROR, f"Could not open experiment: {error}")
            self.recording_state_value.setText(f"Could not open experiment: {error}")

    def _start_recording(self) -> None:
        try:
            self.controller.experiment_recorder.start_recording(
                self.experiment_name_input.text(),
                self.experiment_notes_input.text(),
            )
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            self.controller.state.report_event(EventSeverity.ERROR, str(error))
            self.recording_state_value.setText(str(error))

    def _stop_recording(self) -> None:
        try:
            self.controller.experiment_recorder.stop_recording()
        except (OSError, RuntimeError, TimeoutError) as error:
            self.controller.state.report_event(EventSeverity.ERROR, str(error))
            self.recording_state_value.setText(str(error))

    def _on_recording_requested(self, experiment_id: str, directory: str) -> None:
        self._recording_name = self.experiment_name_input.text().strip() or experiment_id
        self._recording_notes = self.experiment_notes_input.text().strip()
        self._experiment_directory = Path(directory)
        self.open_recording_folder_button.setEnabled(False)
        self.saved_experiment_summary.setVisible(False)
        self.recording_state_value.setText(f"Preparing experiment {experiment_id}...")
        self.current_experiment_path.setText(f"Experiment {experiment_id}: {directory}")
        self.current_experiment_path.setToolTip(directory)

    def _on_recording_started(self, experiment_id: str, directory: str) -> None:
        self.recording_state_value.setText(f"Recording {experiment_id}")
        self.current_experiment_path.setText(
            f"Experiment {experiment_id}; raw capture: {Path(directory) / 'serial_capture.log'}"
        )
        self.current_experiment_path.setToolTip(directory)

    def _on_recording_finished(self, experiment_id: str, status: str) -> None:
        self.recording_state_value.setText(f"{status.title()}: {experiment_id}")
        session = self.controller.experiment_recorder.last_completed_session
        if session is None or session.experiment_id != experiment_id:
            self.controller.state.report_event(
                EventSeverity.WARNING,
                f"Saved experiment metadata unavailable for {experiment_id}.",
            )
            return
        self._show_summary(
            experiment_id=session.experiment_id,
            directory=session.directory,
            manifest=session.manifest,
            sample_count=session.manifest.get("sample_count"),
        )
        self.current_experiment_path.setText(f"Experiment folder: {session.directory}")
        self.current_experiment_path.setToolTip(str(session.directory))

    def _on_recording_failed(self, experiment_id: str, message: str) -> None:
        self.recording_state_value.setText(f"Recording failed: {experiment_id}")
        self.current_experiment_path.setText(message)
        self.current_experiment_path.setToolTip(message)

    def _update_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        recording_active = snapshot.recording in (
            RecordingState.STARTING,
            RecordingState.RECORDING,
            RecordingState.STOPPING,
        )
        live_source_ready = (
            snapshot.data_source == DataSourceMode.MOCK and snapshot.mock_source_active
        ) or (
            snapshot.data_source == DataSourceMode.REAL
            and snapshot.connection == ConnectionState.CONNECTED
        )
        self.start_recording_button.setEnabled(
            live_source_ready and not recording_active and snapshot.motion != MotionState.HOMING
        )
        self.stop_recording_button.setEnabled(snapshot.recording == RecordingState.RECORDING)
        self.experiment_name_input.setEnabled(not recording_active)
        self.experiment_notes_input.setEnabled(not recording_active)
        if snapshot.recording == RecordingState.ERROR:
            # The detailed failure signal supplies the exception and target path.
            return
        if snapshot.recording == RecordingState.SAVED and self._experiment_directory is not None:
            self.open_recording_folder_button.setEnabled(self._experiment_directory.is_dir())

    def _update_readiness(self, snapshot) -> None:
        self.readiness_indicator.setText(snapshot.readiness.value)
        colors = {
            "NOT CONNECTED": ("#66736f", "#eef1ef"),
            "CHECK REQUIRED": ("#8b5b14", "#fff3d8"),
            "BIT PASSED - HOME REQUIRED": ("#8b5b14", "#fff3d8"),
            "SYSTEM READY": ("#205f49", "#e5f4ed"),
            "FAULT": ("#a3322b", "#fbe8e6"),
        }
        foreground, background = colors[snapshot.readiness.value]
        self.readiness_indicator.setStyleSheet(
            f"color: {foreground}; background: {background}; padding: 5px 8px; font-weight: 700;"
        )

    def _show_saved_experiment(self, experiment: LoadedExperiment) -> None:
        self._experiment_directory = experiment.directory
        self.experiment_name_input.setText(experiment.manifest.get("name", ""))
        self.experiment_notes_input.setText(experiment.manifest.get("notes", ""))
        name = experiment.manifest.get("name") or experiment.experiment_id
        status = experiment.manifest.get("completion_status", "UNKNOWN")
        self.recording_state_value.setText(f"Loaded {name} ({status})")
        self.current_experiment_path.setText(f"Experiment folder: {experiment.directory}")
        self.current_experiment_path.setToolTip(str(experiment.directory))
        self._show_summary(
            experiment_id=experiment.experiment_id,
            directory=experiment.directory,
            manifest=experiment.manifest,
            sample_count=experiment.manifest.get("sample_count", len(experiment.telemetry)),
        )

    def _show_summary(
        self,
        *,
        experiment_id: str,
        directory: Path,
        manifest: dict,
        sample_count: object,
    ) -> None:
        data_source = manifest.get("data_source")
        source_mode = data_source.get("mode", "-") if isinstance(data_source, dict) else "-"
        self.saved_name_value.setText(str(manifest.get("name") or experiment_id))
        self.saved_notes_value.setText(str(manifest.get("notes") or "-"))
        self.saved_id_value.setText(experiment_id)
        self.saved_path_value.setText(str(directory))
        self.saved_path_value.setToolTip(str(directory))
        self.saved_status_value.setText(str(manifest.get("completion_status") or "-"))
        self.saved_sample_count_value.setText("-" if sample_count is None else str(sample_count))
        self.saved_data_source_value.setText(str(source_mode))
        self.saved_experiment_summary.setVisible(True)
        self.open_recording_folder_button.setEnabled(directory.is_dir())

    def _open_recording_folder(self) -> None:
        if self._experiment_directory is None or not self._experiment_directory.is_dir():
            self.open_recording_folder_button.setEnabled(False)
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(self._experiment_directory))):
            self.controller.state.report_event(
                EventSeverity.WARNING,
                f"Could not open recording folder: {self._experiment_directory}",
            )
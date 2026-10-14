from pathlib import Path

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QColor, QDesktopServices
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsDropShadowEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QVBoxLayout,
    QWidget,
)

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.angle_prediction import AngleSource
from pendulum_workbench.domain.models import (
    ApplicationSnapshot,
    ConnectionState,
    DataSourceMode,
    EventSeverity,
    MotionState,
    RecordingState,
)
from pendulum_workbench.domain.experiment_run import ExperimentRunState, VerticalSineDefinition
from pendulum_workbench.domain.maintenance import PendulumLatchState
from pendulum_workbench.experiments.repository import LoadedExperiment


class ExperimentPage(QWidget):
    def __init__(self, controller: WorkbenchController) -> None:
        super().__init__()
        self.controller = controller
        self.run_service = controller.experiment_run
        self._recording_name = ""
        self._recording_notes = ""
        self._experiment_directory: Path | None = None

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setObjectName("experimentPageScrollArea")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.content_widget = QWidget()
        self.content_widget.setObjectName("experimentPageContent")
        self.content_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        layout = QVBoxLayout(self.content_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        outer_layout.addWidget(self.scroll_area)
        self.scroll_area.setWidget(self.content_widget)

        recording_panel = QFrame()
        self.setup_panel = recording_panel
        recording_panel.setObjectName("surface")
        recording_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._apply_card_depth(recording_panel)
        recording_layout = QVBoxLayout(recording_panel)
        recording_layout.setContentsMargins(18, 14, 18, 14)
        recording_layout.setSpacing(12)
        recording_heading = QLabel("EXPERIMENT SETUP")
        recording_heading.setObjectName("sectionTitle")
        recording_layout.addWidget(recording_heading)
        self.readiness_indicator = QLabel("CHECK REQUIRED")
        self.readiness_indicator.setObjectName("experimentReadinessIndicator")
        recording_layout.addWidget(self.readiness_indicator)

        latch_row = QHBoxLayout()
        self.latch_state_value = QLabel("Pendulum Latch: UNKNOWN")
        self.latch_state_value.setObjectName("experimentLatchState")
        self.latch_state_value.setMinimumHeight(34)
        latch_row.addWidget(self.latch_state_value, 1)
        self.prepare_latch_button = QPushButton("PREPARE LATCH / HOLD")
        self.prepare_latch_button.setObjectName("prepareExperimentLatchButton")
        self.prepare_latch_button.setMinimumHeight(38)
        self.prepare_latch_button.clicked.connect(self._prepare_latch)
        latch_row.addWidget(self.prepare_latch_button)
        recording_layout.addLayout(latch_row)

        mode_row = QHBoxLayout()
        mode_label = QLabel("EXPERIMENT MODE")
        mode_label.setObjectName("sectionTitle")
        self.experiment_mode_selector = QComboBox()
        self.experiment_mode_selector.addItem("Vertical Sinusoidal Excitation")
        self.experiment_mode_selector.setMinimumHeight(38)
        mode_row.addWidget(mode_label)
        mode_row.addWidget(self.experiment_mode_selector, 1)
        recording_layout.addLayout(mode_row)

        fields = QGridLayout()
        fields.setHorizontalSpacing(12)
        fields.setVerticalSpacing(12)
        amplitude_label = QLabel("PIVOT AMPLITUDE [mm]")
        amplitude_label.setObjectName("sectionTitle")
        self.amplitude_input = QLineEdit()
        self.amplitude_input.setObjectName("experimentAmplitudeInput")
        self.amplitude_input.setPlaceholderText("Required")
        self.amplitude_input.setMinimumHeight(38)
        frequency_label = QLabel("EXCITATION FREQUENCY [Hz]")
        frequency_label.setObjectName("sectionTitle")
        self.frequency_input = QLineEdit()
        self.frequency_input.setObjectName("experimentFrequencyInput")
        self.frequency_input.setPlaceholderText("Required")
        self.frequency_input.setMinimumHeight(38)
        duration_label = QLabel("DURATION [s]")
        duration_label.setObjectName("sectionTitle")
        self.duration_input = QLineEdit()
        self.duration_input.setObjectName("experimentDurationInput")
        self.duration_input.setPlaceholderText("Required")
        self.duration_input.setMinimumHeight(38)
        fields.addWidget(amplitude_label, 0, 0)
        fields.addWidget(self.amplitude_input, 1, 0)
        fields.addWidget(frequency_label, 0, 1)
        fields.addWidget(self.frequency_input, 1, 1)
        fields.addWidget(duration_label, 0, 2)
        fields.addWidget(self.duration_input, 1, 2)

        name_group = QVBoxLayout()
        name_label = QLabel("OPTIONAL EXPERIMENT LABEL")
        name_label.setObjectName("sectionTitle")
        self.experiment_name_input = QLineEdit()
        self.experiment_name_input.setObjectName("experimentNameInput")
        self.experiment_name_input.setPlaceholderText("Optional")
        self.experiment_name_input.setMinimumHeight(38)
        name_group.addWidget(name_label)
        name_group.addWidget(self.experiment_name_input)
        notes_group = QVBoxLayout()
        notes_label = QLabel("NOTES")
        notes_label.setObjectName("sectionTitle")
        self.experiment_notes_input = QLineEdit()
        self.experiment_notes_input.setObjectName("experimentNotesInput")
        self.experiment_notes_input.setPlaceholderText("Optional")
        self.experiment_notes_input.setMinimumHeight(38)
        notes_group.addWidget(notes_label)
        notes_group.addWidget(self.experiment_notes_input)
        fields.addLayout(name_group, 2, 0)
        fields.addLayout(notes_group, 2, 1, 1, 2)
        recording_layout.addLayout(fields)
        self.run_name_preview = QLabel("Final run name includes the measured initial angle.")
        self.run_name_preview.setObjectName("subtle")
        self.run_name_preview.setWordWrap(True)
        recording_layout.addWidget(self.run_name_preview)
        layout.addWidget(recording_panel)

        run_status_panel = QFrame()
        self.run_status_panel = run_status_panel
        run_status_panel.setObjectName("surface")
        run_status_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._apply_card_depth(run_status_panel)
        run_status_layout = QVBoxLayout(run_status_panel)
        run_status_layout.setContentsMargins(18, 14, 18, 14)
        run_status_layout.setSpacing(10)
        run_status_heading = QLabel("RUN STATUS")
        run_status_heading.setObjectName("sectionTitle")
        run_status_layout.addWidget(run_status_heading)
        self.run_state_value = QLabel(ExperimentRunState.READY.value)
        self.run_state_value.setObjectName("experimentRunState")
        self.run_state_value.setAlignment(Qt.AlignmentFlag.AlignCenter)
        run_status_layout.addWidget(self.run_state_value)
        live_values = QHBoxLayout()
        self.run_angle_value = QLabel("Angle: unavailable")
        self.run_angle_value.setObjectName("hardwareInfoValue")
        self.run_position_value = QLabel("Pivot: unavailable")
        self.run_position_value.setObjectName("hardwareInfoValue")
        live_values.addWidget(self.run_angle_value, 1)
        live_values.addWidget(self.run_position_value, 1)
        run_status_layout.addLayout(live_values)
        self.angle_source_value = QLabel("Angle source: UNAVAILABLE")
        self.angle_source_value.setObjectName("subtle")
        run_status_layout.addWidget(self.angle_source_value)
        elapsed_row = QHBoxLayout()
        self.run_elapsed_value = QLabel("Elapsed: 0.0 s")
        self.run_elapsed_value.setObjectName("subtle")
        self.run_progress = QProgressBar()
        self.run_progress.setObjectName("experimentRunProgress")
        self.run_progress.setRange(0, 1000)
        self.run_progress.setValue(0)
        self.run_progress.setTextVisible(False)
        elapsed_row.addWidget(self.run_elapsed_value)
        elapsed_row.addWidget(self.run_progress, 1)
        run_status_layout.addLayout(elapsed_row)
        run_controls = QHBoxLayout()
        self.run_recording_state_value = QLabel("Recording: Off")
        self.run_recording_state_value.setObjectName("subtle")
        run_controls.addWidget(self.run_recording_state_value, 1)
        self.primary_run_button = QPushButton("RUN EXPERIMENT")
        self.primary_run_button.setObjectName("primaryExperimentRunButton")
        self.primary_run_button.setMinimumHeight(52)
        self.primary_run_button.clicked.connect(self._on_primary_run_button)
        run_controls.addWidget(self.primary_run_button)
        run_status_layout.addLayout(run_controls)
        self.run_error_value = QLabel("")
        self.run_error_value.setObjectName("subtle")
        self.run_error_value.setWordWrap(True)
        run_status_layout.addWidget(self.run_error_value)
        layout.addWidget(run_status_panel)

        saved_panel = QFrame()
        self.run_record_panel = saved_panel
        saved_panel.setObjectName("surface")
        saved_panel.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        self._apply_card_depth(saved_panel)
        saved_layout = QVBoxLayout(saved_panel)
        saved_layout.setContentsMargins(18, 14, 18, 14)
        saved_layout.setSpacing(10)
        saved_header = QHBoxLayout()
        saved_heading = QLabel("RUN RECORD")
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

        self.current_experiment_path = QLabel("No active experiment")
        self.current_experiment_path.setObjectName("experimentPathValue")
        self.current_experiment_path.setWordWrap(True)
        self.current_experiment_path.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        saved_layout.addWidget(self.current_experiment_path)

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
        self.run_initial_angle_value = self._summary_value("runInitialAngleValue")
        self.run_started_at_value = self._summary_value("runStartedAtValue")
        self.run_actual_duration_value = self._summary_value("runActualDurationValue")
        self.run_completion_reason_value = self._summary_value("runCompletionReasonValue")
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
        summary.addWidget(self._summary_key("MEASURED INITIAL ANGLE"), 5, 0)
        summary.addWidget(self.run_initial_angle_value, 5, 1)
        summary.addWidget(self._summary_key("STARTED AT"), 5, 2)
        summary.addWidget(self.run_started_at_value, 5, 3)
        summary.addWidget(self._summary_key("ACTUAL DURATION"), 6, 0)
        summary.addWidget(self.run_actual_duration_value, 6, 1)
        summary.addWidget(self._summary_key("COMPLETION REASON"), 6, 2)
        summary.addWidget(self.run_completion_reason_value, 6, 3)
        summary.setColumnStretch(1, 1)
        summary.setColumnStretch(3, 2)
        self.saved_experiment_summary.setVisible(True)
        saved_layout.addWidget(self.saved_experiment_summary)
        layout.addWidget(saved_panel)
        layout.addStretch(1)

        self.controller.state.snapshot_changed.connect(self._update_snapshot)
        recorder = self.controller.experiment_recorder
        recorder.recording_requested.connect(self._on_recording_requested)
        recorder.recording_started.connect(self._on_recording_started)
        recorder.recording_finished.connect(self._on_recording_finished)
        recorder.recording_failed.connect(self._on_recording_failed)
        self.run_service.snapshot_changed.connect(self._update_run_snapshot)
        self.controller.state.telemetry_received.connect(self._update_run_live_values)
        self.amplitude_input.textChanged.connect(self._update_setup_validity)
        self.frequency_input.textChanged.connect(self._update_setup_validity)
        self.duration_input.textChanged.connect(self._update_setup_validity)
        self.experiment_name_input.textChanged.connect(self._update_name_preview)
        self.controller.experiment_loaded.connect(self._show_saved_experiment)
        self.controller.maintenance.snapshot_changed.connect(self._update_readiness)
        self._update_snapshot(self.controller.state.snapshot)
        self._update_readiness(self.controller.maintenance.snapshot)
        self._update_run_snapshot(self.run_service.snapshot)

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
            self.run_error_value.setText(f"Could not open experiment: {error}")

    def _on_recording_requested(self, experiment_id: str, directory: str) -> None:
        self._recording_name = self.run_service.snapshot.generated_name or experiment_id
        self._recording_notes = self.experiment_notes_input.text().strip()
        self._experiment_directory = Path(directory)
        self.open_recording_folder_button.setEnabled(False)
        self.current_experiment_path.setText(f"Experiment {experiment_id}: {directory}")
        self.current_experiment_path.setToolTip(directory)
        self.run_recording_state_value.setText("Recording: Starting")

    def _on_recording_started(self, experiment_id: str, directory: str) -> None:
        self.run_recording_state_value.setText("Recording: Recording")
        self.current_experiment_path.setText(
            f"Experiment {experiment_id}; raw capture: {Path(directory) / 'serial_capture.log'}"
        )
        self.current_experiment_path.setToolTip(directory)

    def _on_recording_finished(self, experiment_id: str, status: str) -> None:
        self.run_recording_state_value.setText(f"Recording: {status.title()}")
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
        self.run_recording_state_value.setText(f"Recording: Failed ({experiment_id})")
        self.run_error_value.setText(message)
        self.current_experiment_path.setText(message)
        self.current_experiment_path.setToolTip(message)

    def _definition_from_fields(self) -> VerticalSineDefinition:
        try:
            amplitude = float(self.amplitude_input.text().strip())
            frequency = float(self.frequency_input.text().strip())
            duration = float(self.duration_input.text().strip())
        except ValueError as error:
            raise ValueError("Enter numeric amplitude, frequency, and duration values") from error
        definition = VerticalSineDefinition(
            amplitude,
            frequency,
            duration,
            label=self.experiment_name_input.text(),
            notes=self.experiment_notes_input.text(),
        )
        definition.validate()
        return definition

    def _on_primary_run_button(self) -> None:
        run_state = self.run_service.snapshot.state
        if run_state in (ExperimentRunState.PREPARING, ExperimentRunState.RUNNING):
            self.run_service.stop()
            return
        if run_state == ExperimentRunState.STOPPING:
            return
        try:
            self.run_error_value.setText("")
            self.run_service.start(self._definition_from_fields())
        except (OSError, RuntimeError, TimeoutError, ValueError) as error:
            self.run_error_value.setText(str(error))
            self.controller.state.report_event(EventSeverity.WARNING, str(error))
        self._update_setup_validity()

    def _prepare_latch(self) -> None:
        try:
            self.controller.maintenance.command_latch(PendulumLatchState.HOLD)
        except (RuntimeError, ValueError, OSError) as error:
            self.controller.state.report_event(EventSeverity.WARNING, str(error))
        self._update_latch_controls()

    def _update_latch_controls(self) -> None:
        maintenance = self.controller.maintenance
        maintenance_snapshot = maintenance.snapshot
        latch_state = maintenance_snapshot.latch_state
        state_colors = {
            PendulumLatchState.UNKNOWN: ("#64716d", "#eef1ef"),
            PendulumLatchState.COMMANDING_HOLD: ("#8b5b14", "#fff3d8"),
            PendulumLatchState.COMMANDING_OPEN: ("#8b5b14", "#fff3d8"),
            PendulumLatchState.HOLD: ("#205f49", "#e5f4ed"),
            PendulumLatchState.OPEN: ("#386273", "#eaf2f5"),
            PendulumLatchState.UNCONFIRMED: ("#a3322b", "#fbe8e6"),
        }
        foreground, background = state_colors[latch_state]
        self.latch_state_value.setText(f"Pendulum Latch: {latch_state.value}")
        self.latch_state_value.setToolTip(maintenance_snapshot.latch_message)
        self.latch_state_value.setStyleSheet(
            f"color: {foreground}; background: {background}; padding: 6px 9px; font-weight: 700;"
        )

        if latch_state == PendulumLatchState.HOLD:
            self.prepare_latch_button.setText("HOLD CONFIRMED")
        elif latch_state == PendulumLatchState.COMMANDING_HOLD:
            self.prepare_latch_button.setText("COMMANDING HOLD...")
        else:
            self.prepare_latch_button.setText("PREPARE LATCH / HOLD")

        app_snapshot = self.controller.state.snapshot
        run_state = self.run_service.snapshot.state
        experiment_active = run_state in (
            ExperimentRunState.PREPARING,
            ExperimentRunState.RUNNING,
            ExperimentRunState.STOPPING,
        )
        incompatible_motion = (
            maintenance.is_manual_motion_active
            or app_snapshot.motion in (
                MotionState.HOMING,
                MotionState.RUNNING,
                MotionState.STOPPING,
            )
            or app_snapshot.hardware_status.active_motion_mode.strip().lower() != "idle"
        )
        blocking_fault = bool(
            app_snapshot.hardware_status.fault_known
            and app_snapshot.hardware_status.fault
        )
        self.prepare_latch_button.setEnabled(
            app_snapshot.connection == ConnectionState.CONNECTED
            and not experiment_active
            and not incompatible_motion
            and not maintenance.is_busy
            and not blocking_fault
            and latch_state != PendulumLatchState.HOLD
        )

    def _update_setup_validity(self, *_args) -> None:
        self._update_latch_controls()
        locked = self.run_service.setup_locked
        for widget in (
            self.experiment_mode_selector,
            self.amplitude_input,
            self.frequency_input,
            self.duration_input,
            self.experiment_name_input,
            self.experiment_notes_input,
        ):
            widget.setEnabled(not locked)

        run_state = self.run_service.snapshot.state
        if run_state in (ExperimentRunState.PREPARING, ExperimentRunState.RUNNING):
            self.primary_run_button.setText("STOP EXPERIMENT")
            self.primary_run_button.setEnabled(True)
            return
        if run_state == ExperimentRunState.STOPPING:
            self.primary_run_button.setText("STOPPING...")
            self.primary_run_button.setEnabled(False)
            return

        self.primary_run_button.setText("RUN EXPERIMENT")
        try:
            definition = self._definition_from_fields()
            self.run_service.validate_start(definition)
        except (RuntimeError, ValueError) as error:
            self.primary_run_button.setEnabled(False)
            self.run_error_value.setText(str(error))
        else:
            self.primary_run_button.setEnabled(True)
            self.run_error_value.setText("")
        self._update_name_preview()

    def _update_name_preview(self, *_args) -> None:
        try:
            definition = self._definition_from_fields()
        except ValueError:
            self.run_name_preview.setText("Enter setup values; final name will include the measured initial angle.")
            return
        baseline_hint = "Stationary pivot baseline. " if definition.amplitude_mm == 0.0 else ""
        self.run_name_preview.setText(
            f"{baseline_hint}Run name preview: {definition.partial_name()} (initial angle added at start)"
        )

    def _update_run_snapshot(self, snapshot) -> None:
        self.run_state_value.setText(snapshot.state.value)
        self.run_recording_state_value.setText(f"Recording: {snapshot.recording_state}")
        definition = snapshot.definition
        if definition is not None:
            self.run_elapsed_value.setText(
                f"Elapsed: {snapshot.elapsed_s:.1f} / {definition.duration_s:.1f} s"
            )
            fraction = min(1.0, max(0.0, snapshot.elapsed_s / definition.duration_s))
            self.run_progress.setValue(int(fraction * 1000))
        else:
            self.run_elapsed_value.setText("Elapsed: 0.0 s")
            self.run_progress.setValue(0)
        self.saved_name_value.setText(snapshot.generated_name or "-")
        self.saved_id_value.setText(snapshot.experiment_id or "-")
        self.saved_sample_count_value.setText(str(snapshot.sample_count))
        self.run_initial_angle_value.setText(
            "-" if snapshot.measured_initial_angle_deg is None
            else f"{snapshot.measured_initial_angle_deg:+.2f} deg"
        )
        self.run_started_at_value.setText(snapshot.started_at or "-")
        self.run_actual_duration_value.setText(
            "-" if snapshot.actual_elapsed_duration_s is None
            else f"{snapshot.actual_elapsed_duration_s:.3f} s"
        )
        self.run_completion_reason_value.setText(snapshot.completion_reason or "-")
        source = snapshot.angle_source.value.upper()
        if snapshot.angle_source == AngleSource.PREDICTED and snapshot.prediction_age_ms is not None:
            source += f" ({snapshot.prediction_age_ms:.0f} ms)"
        self.angle_source_value.setText(f"Angle source: {source}")
        self.run_error_value.setText(snapshot.error_message)
        if snapshot.directory:
            self.current_experiment_path.setText(snapshot.directory)
            self.current_experiment_path.setToolTip(snapshot.directory)
        self._update_setup_validity()

    def _update_run_live_values(self, sample) -> None:
        if sample.source != "real":
            return
        self.run_angle_value.setText(
            "Angle: unavailable" if sample.angle_deg is None else f"Angle: {sample.angle_deg:+.2f} deg"
        )
        self.run_position_value.setText(
            "Pivot: unavailable" if sample.position_mm is None else f"Pivot: {sample.position_mm:+.2f} mm"
        )

    def _update_snapshot(self, snapshot: ApplicationSnapshot) -> None:
        self._update_setup_validity()
        if snapshot.recording == RecordingState.ERROR:
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
        self._update_setup_validity()

    def _show_saved_experiment(self, experiment: LoadedExperiment) -> None:
        self._experiment_directory = experiment.directory
        experiment_metadata = experiment.manifest.get("experiment", {})
        self.experiment_name_input.setText(
            experiment_metadata.get("optional_label", experiment.manifest.get("name", ""))
        )
        self.experiment_notes_input.setText(experiment.manifest.get("notes", ""))
        name = experiment.manifest.get("name") or experiment.experiment_id
        status = experiment.manifest.get("completion_status", "UNKNOWN")
        self.run_recording_state_value.setText(f"Recording: Loaded ({status})")
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
        experiment_metadata = manifest.get("experiment", {})
        measured_angle = experiment_metadata.get("measured_initial_angle_deg")
        self.run_initial_angle_value.setText(
            "-" if measured_angle is None else f"{float(measured_angle):+.2f} deg"
        )
        self.run_started_at_value.setText(str(manifest.get("started_at") or "-"))
        actual_duration = experiment_metadata.get("actual_elapsed_duration_s")
        self.run_actual_duration_value.setText(
            "-" if actual_duration is None else f"{float(actual_duration):.3f} s"
        )
        self.run_completion_reason_value.setText(
            str(experiment_metadata.get("completion_reason") or manifest.get("interruption_reason") or "-")
        )
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
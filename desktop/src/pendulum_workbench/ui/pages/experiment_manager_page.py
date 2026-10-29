"""Experiment browser with reversible archive and Excel index export."""
import os
import csv
import logging
import math
import shutil
import sys
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QProcess, QSettings
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox, QFileDialog,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QAbstractItemView,
    QFormLayout, QGroupBox, QScrollArea, QGridLayout, QFrame, QHeaderView, QSplitter,
)

from pendulum_workbench.experiments.repository import ExperimentRepository
from pendulum_workbench.experiments.simulation_cache import inspect_simulation
from pendulum_workbench.experiments.target_metrics import target_metrics


_LOGGER = logging.getLogger(__name__)


class SortableExperimentItem(QTableWidgetItem):
    """Compare stored sort keys without calling Qt's re-entrant comparator."""
    SORT_ROLE = int(Qt.ItemDataRole.UserRole) + 1

    def __lt__(self, other):
        left = self.data(self.SORT_ROLE)
        right = other.data(self.SORT_ROLE)
        if left is not None and right is not None:
            return left < right
        if left is not None:
            return False
        if right is not None:
            return True
        return self.text().casefold() < other.text().casefold()


class ExperimentManagerPage(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.repository = controller.experiment_recorder.repository
        self._records = []
        self._matlab_process = None
        self._matlab_output = []
        self._matlab_log_path = None
        self._active_simulation_folder = None
        self._settings = QSettings('PendulumWorkbench', 'PendulumWorkbench')
        layout = QVBoxLayout(self)
        heading = QLabel("Experiment Library")
        heading.setStyleSheet("font-size: 22px; font-weight: 700;")
        layout.addWidget(heading)
        toolbar = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search name, ID, type or parameters")
        self.search.textChanged.connect(self._filter)
        toolbar.addWidget(self.search)
        self.visibility = QComboBox()
        self.visibility.addItems(["Active", "Archived", "All"])
        self.visibility.currentTextChanged.connect(self._filter)
        toolbar.addWidget(self.visibility)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        toolbar.addWidget(refresh)
        export = QPushButton("Sync Excel")
        export.clicked.connect(self.export_excel)
        toolbar.addWidget(export)
        layout.addLayout(toolbar)
        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels(["Date", "Experiment", "Mode", "Duration", "Initial angle", "Peak angle", "Target angle", "Target achieved", "Target time", "Status", "Simulation"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for index, width in enumerate((145, 290, 132, 91, 106, 98, 103, 115, 108, 109, 130)):
            self.table.setColumnWidth(index, width)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(37)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setStyleSheet("""
            QTableWidget { background: #ffffff; alternate-background-color: #f5f8f6;
                border: 1px solid #d8e1dc; border-radius: 7px;
                selection-background-color: #d4e9df; selection-color: #184c3c; }
            QTableWidget::item { padding: 7px 10px; border-bottom: 1px solid #e7eee9; }
            QTableWidget::item:selected { background: #d4e9df; color: #184c3c; }
            QHeaderView::section { background: #e5eee9; color: #36584c;
                font-weight: 700; padding: 9px; border: 0;
                border-bottom: 2px solid #c5d8cd; }
        """)
        self.table.setMinimumHeight(220)
        self.table.itemSelectionChanged.connect(self._show_details)
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self.table)
        # Read-only dashboard: small, grouped fields rather than a long metadata dump.
        detail_scroll = QScrollArea()
        detail_scroll.setWidgetResizable(True)
        detail_scroll.setFrameShape(QFrame.Shape.NoFrame)
        detail_content = QWidget()
        detail_layout = QVBoxLayout(detail_content)
        detail_layout.setContentsMargins(4, 8, 4, 8)
        detail_layout.setSpacing(12)
        self.detail_labels = {}
        sections = (
            ("Overview", (("Name", "Experiment ID", "Created"), ("Mode", "Completion", "Archived"))),
            ("Excitation settings", (("Amplitude", "Frequency", "Phase"), ("Target angle", "Commanded duration", "Duration"))),
            ("Recorded results", (("Initial angle", "Peak angle", "Amplification"), ("Peak time", "Samples", "Raw lines"), ("Target achieved", "Target time", "Target angle"))),
            ("Simulation", (("Simulation status", "Comparison status", "Simulation files"),)),
            ("Storage & notes", (("Notes", "Folder"),)),
        )
        for section_title, rows in sections:
            group = QGroupBox(section_title)
            group.setStyleSheet(
                "QGroupBox { background: #ffffff; border: 1px solid #d8e1dc;"
                " border-radius: 8px; margin-top: 13px; padding: 12px 8px 8px 8px;"
                " font-weight: 700; color: #287b68; }"
                "QGroupBox::title { subcontrol-origin: margin; left: 12px;"
                " padding: 0 5px; }"
            )
            grid = QGridLayout(group)
            grid.setHorizontalSpacing(24)
            grid.setVerticalSpacing(12)
            for row_index, pair in enumerate(rows):
                for col_index, key in enumerate(pair):
                    cell = QWidget()
                    cell_layout = QVBoxLayout(cell)
                    cell_layout.setContentsMargins(3, 2, 3, 2)
                    cell_layout.setSpacing(3)
                    caption = QLabel(key.upper())
                    caption.setStyleSheet("color: #74847d; font-size: 10px; font-weight: 600;")
                    value = QLabel("—")
                    value.setWordWrap(True)
                    value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
                    value.setStyleSheet("color: #176c55; font-size: 19px; font-weight: 700;" if key in ("Initial angle", "Peak angle", "Amplification") else "color: #24363a; font-size: 13px; font-weight: 600;")
                    cell_layout.addWidget(caption)
                    cell_layout.addWidget(value)
                    grid.addWidget(cell, row_index, col_index)
                    self.detail_labels[key] = value
            grid.setColumnStretch(0, 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnStretch(2, 1)
            detail_layout.addWidget(group)
        detail_layout.addStretch()
        detail_scroll.setWidget(detail_content)
        splitter.addWidget(detail_scroll)
        splitter.setSizes([340, 340])
        layout.addWidget(splitter, 1)
        actions = QHBoxLayout()
        for label, callback in [
            ("Open Simulation Folder", self.open_simulation_folder),
            ("Run / Reuse Simscape", self.run_simscape),
            ("Archive / Restore", self.toggle_archive),
            ("Delete Permanently", self.delete_selected),
        ]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.simulation_progress = QLabel('MATLAB: idle')
        self.simulation_progress.setWordWrap(True)
        layout.addWidget(self.simulation_progress)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        controller.experiment_recorder.recording_finished.connect(lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        self.repository = self.controller.experiment_recorder.repository
        selected = self._selected()
        selected_path = selected[0] if selected else None
        self._records = self.repository.list_manifests()
        self._filter()
        if selected_path is not None:
            for row in range(self.table.rowCount()):
                item = self.table.item(row, 0)
                if item is not None and Path(item.data(Qt.ItemDataRole.UserRole)) == selected_path:
                    self.table.selectRow(row)
                    break

    @staticmethod
    def _friendly_mode(mode: object) -> str:
        raw = str(mode or "Recording")
        return {
            "FREE_SWING": "Free Swing",
            "VERTICAL_SINUSOIDAL_EXCITATION": "Sinusoidal",
            "AUTO_RESONANCE_PARAMETRIC": "Auto Resonance",
            "PHASE_TRACKED_SINE_AUTO_RESONANCE": "Phase Tracked",
        }.get(raw, raw)

    def _filter(self, *_):
        query = self.search.text().casefold().strip()
        visibility = self.visibility.currentText()
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        for directory, manifest in self._records:
            archived = bool(manifest.get("archived", False))
            if visibility == "Active" and archived or visibility == "Archived" and not archived:
                continue
            details = manifest.get("experiment") or {}
            if not isinstance(details, dict):
                details = {}
            kind = str(details.get("experiment_mode") or "Recording")
            params = ", ".join(f"{k}={v}" for k, v in details.items()
                               if k != "experiment_mode" and isinstance(v, (str, int, float, bool)))
            duration = details.get("actual_elapsed_duration_s")
            if duration is None:
                duration = details.get("commanded_duration_s")
            if duration is None:
                duration = details.get("duration_s")
            peak = manifest.get("peak_absolute_angle_deg")
            initial = details.get("measured_initial_angle_deg")
            targets = target_metrics(directory, manifest)
            _LOGGER.info(
                "Experiment Library record: experiment_id=%s target_angle=%s "
                "target_achieved=%s target_time=%s completion_reason=%s "
                "recording_state=%s",
                manifest.get("experiment_id"),
                targets["target_angle_deg"],
                targets["target_achieved"],
                targets["target_time_s"],
                details.get("completion_reason"),
                manifest.get("completion_status"),
            )
            fields = [
                str(manifest.get("created_at") or "")[:16].replace("T", " "),
                str(manifest.get("name") or ""),
                self._friendly_mode(kind),
                f"{duration:.1f} s" if isinstance(duration, (int, float)) else "—",
                self._display_number(initial, "°"),
                self._display_number(peak, "°"),
                self._display_number(targets["target_angle_deg"], "°"),
                ("Yes" if targets["target_achieved"] else "No")
                    if targets["target_achieved"] is not None else "—",
                self._display_number(targets["target_time_s"], " s"),
                "Archived" if archived else str(manifest.get("completion_status") or ""),
                {"ready": "Saved", "stale": "Stale", "incomplete": "Incomplete",
                 "missing": "Not simulated"}[inspect_simulation(directory).state],
            ]
            searchable = " ".join(fields + [params, str(manifest.get("experiment_id") or "")])
            if query and query not in searchable.casefold():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate(fields):
                item = SortableExperimentItem(value)
                item.setData(Qt.ItemDataRole.UserRole, str(directory))
                if col in (3, 4, 5, 6, 8):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                elif col in (7, 9, 10):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col in (3, 4, 5, 6, 8):
                    numeric = {3: duration, 4: initial, 5: peak,
                               6: targets["target_angle_deg"], 8: targets["target_time_s"]}[col]
                    if isinstance(numeric, (int, float)) and math.isfinite(numeric):
                        item.setData(Qt.ItemDataRole.UserRole + 1, float(numeric))
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self.summary.setText(f"{self.table.rowCount()} experiments shown")
        self._show_details()

    def _selected(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        item = self.table.item(row, 0)
        if item is None:
            return None
        path = Path(item.data(Qt.ItemDataRole.UserRole))
        for directory, manifest in self._records:
            if directory == path:
                return directory, manifest
        return None

    @staticmethod
    def _display_number(value, unit="", precision=2):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f"{value:.{precision}f}{unit}"
        return "—"

    def _show_details(self):
        selected = self._selected()
        if selected is None:
            for label in self.detail_labels.values():
                label.setText("—")
            return
        directory, manifest = selected
        details = manifest.get("experiment") or {}
        if not isinstance(details, dict):
            details = {}
        duration = details.get("actual_elapsed_duration_s")
        if duration is None:
            duration = details.get("duration_s")
        initial_angle = details.get("measured_initial_angle_deg")
        peak_angle = manifest.get("peak_absolute_angle_deg")
        amplification = (abs(peak_angle) / abs(initial_angle)
                         if isinstance(initial_angle, (int, float)) and not isinstance(initial_angle, bool)
                         and isinstance(peak_angle, (int, float)) and not isinstance(peak_angle, bool)
                         and math.isfinite(initial_angle) and math.isfinite(peak_angle)
                         and abs(initial_angle) > 1e-6 else None)
        peak_elapsed = self._peak_elapsed_time(directory, manifest)
        simulation_status = inspect_simulation(directory)
        comparison_path = directory / "simulation" / "ar_comparison_summary.json"
        if not comparison_path.is_file():
            comparison_path = directory / "simulation" / "comparison_summary.json"
        comparison_status = "Not compared"
        if comparison_path.is_file():
            try:
                import json
                summary = json.loads(comparison_path.read_text(encoding="utf-8"))
                comparison_status = str(summary.get("status") or "Preliminary")
                if simulation_status.state != "ready":
                    comparison_status = "Stale"
            except (OSError, ValueError, TypeError):
                comparison_status = "Incomplete"
        targets = target_metrics(directory, manifest)
        values = {
            "Name": manifest.get("name"),
            "Experiment ID": manifest.get("experiment_id"),
            "Created": manifest.get("created_at"),
            "Mode": self._friendly_mode(details.get("experiment_mode", "Recording")),
            "Completion": manifest.get("completion_status"),
            "Archived": "Yes" if manifest.get("archived") else "No",
            "Amplitude": self._display_number(details.get("commanded_amplitude_mm"), " mm"),
            "Frequency": self._display_number(details.get("commanded_frequency_hz"), " Hz", 3),
            "Phase": self._display_number(details.get("commanded_phase_deg"), "°"),
            "Target angle": self._display_number(targets["target_angle_deg"], "°"),
            "Target achieved": ("Yes" if targets["target_achieved"] else "No")
                if targets["target_achieved"] is not None else "—",
            "Target time": self._display_number(targets["target_time_s"], " s"),
            "Commanded duration": self._display_number(details.get("commanded_duration_s"), " s"),
            "Duration": self._display_number(duration, " s"),
            "Initial angle": self._display_number(details.get("measured_initial_angle_deg"), "°"),
            "Peak angle": self._display_number(manifest.get("peak_absolute_angle_deg"), "°"),
            "Amplification": self._display_number(amplification, "×"),
            "Peak time": self._display_number(peak_elapsed, " s"),
            "Samples": manifest.get("sample_count"),
            "Raw lines": manifest.get("raw_line_count"),
            "Notes": manifest.get("notes"),
            "Folder": str(directory),
            "Simulation status": simulation_status.message,
            "Comparison status": comparison_status,
            "Simulation files": str(simulation_status.folder),
        }
        for key, label in self.detail_labels.items():
            value = values.get(key)
            label.setText(str(value) if value is not None and value != "" else "—")
            label.setToolTip(str(value) if value is not None else "")

    @staticmethod
    def _peak_elapsed_time(directory, manifest):
        """Rebase recorded peak time against the first valid telemetry timestamp."""
        saved_elapsed = manifest.get("peak_angle_elapsed_s")
        if isinstance(saved_elapsed, (int, float)) and math.isfinite(saved_elapsed):
            return saved_elapsed
        raw_peak = manifest.get("peak_angle_time_s")
        if not isinstance(raw_peak, (int, float)) or not math.isfinite(raw_peak):
            return None
        path = directory / "telemetry.csv"
        try:
            with path.open("r", encoding="utf-8", newline="") as source:
                reader = csv.DictReader(source)
                for row in reader:
                    value = row.get("time_s") or row.get("device_time_s")
                    try:
                        first_time = float(value)
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(first_time):
                        continue
                    elapsed = raw_peak - first_time
                    return elapsed if elapsed >= -0.001 else None
        except OSError:
            return None
        return None

    def _simscape_dialog(self, title, message, icon=QMessageBox.Icon.Information):
        """Use a readable light dialog independent of the app's dark QSS."""
        dialog = QMessageBox(self)
        dialog.setWindowTitle(title)
        dialog.setIcon(icon)
        dialog.setText(message)
        dialog.setStyleSheet(
            "QMessageBox { background-color: #f8faf9; color: #24363a; }"
            "QMessageBox QLabel { background: transparent; color: #24363a; "
            "font-size: 13px; min-width: 340px; max-width: 540px; }"
            "QMessageBox QPushButton { color: #24363a; background: #edf2ef;"
            " border: 1px solid #aebdb4; padding: 7px 12px; border-radius: 5px; }"
        )
        return dialog

    def _show_simscape_message(self, title, message, warning=False):
        kind = QMessageBox.Icon.Warning if warning else QMessageBox.Icon.Information
        dialog = self._simscape_dialog(title, message, kind)
        dialog.addButton("OK", QMessageBox.ButtonRole.AcceptRole)
        dialog.exec()

    def open_simulation_folder(self):
        selected = self._selected()
        if selected is None:
            return
        folder = inspect_simulation(selected[0]).folder
        if not folder.is_dir():
            self._show_simscape_message(
                "No simulation saved",
                "This experiment has no saved simulation yet. Use Run / Reuse Simscape first."
            )
            return
        from PySide6.QtGui import QDesktopServices
        from PySide6.QtCore import QUrl
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def run_simscape(self):
        """Run MATLAB asynchronously. MATLAB decides whether cache is reusable."""
        selected = self._selected()
        if selected is None:
            return
        if self._matlab_process is not None and self._matlab_process.state() != QProcess.ProcessState.NotRunning:
            self._show_simscape_message(
                "MATLAB is running",
                "The simulation process is still active. Progress appears below the buttons; "
                "the complete output is in derived/matlab_run.log."
            )
            return
        experiment_dir, manifest = selected
        cache = inspect_simulation(experiment_dir)
        if cache.state == "ready":
            # Saved results can be opened without MATLAB startup. Only MATLAB
            # can verify full model/dictionary/source-code freshness.
            dialog = self._simscape_dialog(
                "Existing simulation",
                "Saved simulation results exist for this experiment.\n\n"
                "Open saved files instantly, or launch MATLAB to verify "
                "model and code changes.",
                QMessageBox.Icon.Question,
            )
            open_button = dialog.addButton("Open Saved Results", QMessageBox.ButtonRole.AcceptRole)
            revalidate_button = dialog.addButton("Revalidate in MATLAB", QMessageBox.ButtonRole.ActionRole)
            dialog.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
            dialog.exec()
            clicked = dialog.clickedButton()
            if clicked is open_button:
                self.open_simulation_folder()
                self.simulation_progress.setText("MATLAB: saved simulation reused without startup")
                return
            if clicked is not revalidate_button:
                return
        mode = (manifest.get("experiment") or {}).get("experiment_mode")
        if mode not in ("FREE_SWING", "VERTICAL_SINUSOIDAL_EXCITATION", "AUTO_RESONANCE_PARAMETRIC"):
            QMessageBox.information(self, "Simscape", "This experiment mode has no MATLAB simulation wrapper yet.")
            return
        choice = self._simscape_dialog(
            "MATLAB connection",
            "Choose how to run this simulation:\n"
            "Use Open MATLAB connects to a shared existing session.\n"
            "Start New MATLAB launches a separate process.",
            QMessageBox.Icon.Question,
        )
        shared_button = choice.addButton("Use Open MATLAB", QMessageBox.ButtonRole.AcceptRole)
        separate_button = choice.addButton("Start New MATLAB", QMessageBox.ButtonRole.ActionRole)
        choice.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
        choice.exec()
        chosen = choice.clickedButton()
        if chosen not in (shared_button, separate_button):
            return
        use_shared = chosen is shared_button

        if not use_shared:
            matlab_exe = str(self._settings.value("simscape/matlab_executable", "") or "")
            if not matlab_exe or not Path(matlab_exe).is_file():
                matlab_exe = shutil.which("matlab") or ""
            if not matlab_exe:
                matlab_exe, _ = QFileDialog.getOpenFileName(
                    self, "Choose matlab.exe", "", "MATLAB executable (matlab.exe);;All files (*)"
                )
                if not matlab_exe:
                    return
            self._settings.setValue("simscape/matlab_executable", matlab_exe)
        thesis_root = str(self._settings.value("simscape/thesis_root", "") or "")
        simulation_dir = Path(thesis_root) / "03_MATLAB" / "Simulation"
        model_dir = Path(thesis_root) / "04_Simscape" / "Models"
        if not (simulation_dir / "runExperimentSimulation.m").is_file() or not (model_dir / "Pendulum_Simscape_Model.slx").is_file():
            thesis_root = QFileDialog.getExistingDirectory(
                self, "Select your Thesis repository root (containing 03_MATLAB and 04_Simscape)",
                str(Path.home())
            )
            if not thesis_root:
                return
            simulation_dir = Path(thesis_root) / "03_MATLAB" / "Simulation"
            model_dir = Path(thesis_root) / "04_Simscape" / "Models"
            if not (simulation_dir / "runExperimentSimulation.m").is_file() or not (model_dir / "Pendulum_Simscape_Model.slx").is_file():
                QMessageBox.warning(self, "Simscape", "The chosen folder does not contain the MATLAB runner and Simscape model.")
                return
            self._settings.setValue("simscape/thesis_root", thesis_root)

        def quote(value):
            # MATLAB single quoted string with doubled embedded apostrophes.
            return "'" + str(value).replace("'", "''") + "'"

        # Match the interactive MATLAB environment: the Thesis MATLAB
        # Project owns the path configuration for CAD/Simscape assets.
        # Opening a separate -batch MATLAB process without its project
        # causes imported-geometry blocks to fail with missing files.
        matlab_batch = (
            "project=openProject(" + quote(thesis_root) + ");"
            "cd(project.RootFolder);"
            "fprintf('Project loaded: %s\\n',project.Name);"
            "runExperimentSimulation(" + quote(experiment_dir) + ");"
            "arManifest=jsondecode(fileread(fullfile(" + quote(experiment_dir) + ","
            + quote("manifest.json") + ")));"
            "if strcmp(string(arManifest.experiment.experiment_mode),'AUTO_RESONANCE_PARAMETRIC');"
            "compareExperiment(" + quote(experiment_dir) + ",Plot=false,SaveResults=true);"
            "end;"
        )
        # The process is asynchronous. MATLAB can take substantial time
        # to initialize Simulink and compile a physical model.
        log_dir = experiment_dir / "derived"
        log_dir.mkdir(parents=True, exist_ok=True)
        self._matlab_log_path = log_dir / "matlab_run.log"
        self._active_simulation_folder = experiment_dir
        self._matlab_log_path.write_text(
            ("Shared MATLAB Engine" if use_shared else "New MATLAB -batch") + " run for: " + str(experiment_dir) + "\\n"
            + "Command: runExperimentSimulation(experiment folder)\\n",
            encoding="utf-8",
        )
        self._matlab_output = []
        process = QProcess(self)
        self._matlab_process = process
        if use_shared:
            process.setProgram(sys.executable)
            if getattr(sys, "frozen", False):
                process.setArguments([
                    "--shared-matlab-runner", str(thesis_root), str(experiment_dir),
                ])
            else:
                process.setArguments([
                    "-m", "pendulum_workbench.experiments.shared_matlab_runner",
                    str(thesis_root), str(experiment_dir),
                ])
        else:
            process.setProgram(matlab_exe)
            process.setArguments(["-batch", matlab_batch])
        process.setWorkingDirectory(str(thesis_root))
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._on_matlab_output)
        process.started.connect(self._on_matlab_started)
        process.finished.connect(self._on_matlab_finished)
        process.errorOccurred.connect(self._on_matlab_error)
        self.simulation_progress.setText("MATLAB: launching for " + experiment_dir.name)
        process.start()

    def _on_matlab_started(self):
        self.simulation_progress.setText("MATLAB: running — monitor derived/matlab_run.log")

    def _on_matlab_output(self):
        if self._matlab_process is None:
            return
        output = bytes(self._matlab_process.readAllStandardOutput()).decode(
            "utf-8", errors="replace"
        )
        if not output:
            return
        self._matlab_output.append(output)
        if self._matlab_log_path is not None:
            with self._matlab_log_path.open("a", encoding="utf-8") as log:
                log.write(output)
        messages = [line.strip() for line in output.splitlines() if line.strip()]
        if messages:
            self.simulation_progress.setText("MATLAB: " + messages[-1][:150])

    def _on_matlab_finished(self, exit_code, _exit_status):
        self._on_matlab_output()
        log_path = self._matlab_log_path
        self._matlab_process = None
        self.refresh()
        status = (inspect_simulation(self._active_simulation_folder).state
                  if self._active_simulation_folder is not None else "unknown")
        if exit_code == 0:
            self.simulation_progress.setText("MATLAB: finished — simulation " + status)
            self._show_simscape_message(
                "Simscape completed",
                "MATLAB finished successfully.\n"
                "Simulation status: " + status.capitalize() + "\n\n"
                "Results are in the selected experiment's simulation/ folder.\n"
                "The complete diagnostic output was saved to:\n" + str(log_path),
            )
        else:
            self.simulation_progress.setText("MATLAB: failed — inspect " + str(log_path))
            self._show_simscape_message(
                "Simscape failed",
                "MATLAB exited with code " + str(exit_code) +
                ".\n\nSee the full diagnostic log:\n" + str(log_path),
                warning=True,
            )

    def _on_matlab_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.simulation_progress.setText("MATLAB: failed to launch")
            self._show_simscape_message(
                "Unable to start MATLAB",
                "Check matlab.exe. See diagnostic log:\n" + str(self._matlab_log_path),
                warning=True,
            )

    def toggle_archive(self):
        selected = self._selected()
        if selected is None:
            return
        directory, manifest = selected
        updated = dict(manifest)
        updated["archived"] = not bool(manifest.get("archived", False))
        updated["archived_at"] = datetime.now().astimezone().isoformat() if updated["archived"] else None
        try:
            self.repository.write_manifest(directory, updated)
            self.refresh()
            self._sync_excel_if_available()
        except OSError as exc:
            QMessageBox.warning(self, "Archive experiment", str(exc))

    def delete_selected(self):
        selected = self._selected()
        if selected is None:
            return
        directory, manifest = selected
        if directory.resolve().parent != self.repository.root.resolve():
            QMessageBox.warning(self, "Unsafe path", "Experiment directory is outside the storage root.")
            return
        if manifest.get("completion_status") == "RECORDING":
            QMessageBox.warning(self, "Recording active", "A recording cannot be deleted while active.")
            return
        answer = QMessageBox.question(
            self, "Permanently delete experiment",
            f"Delete '{manifest.get('name', directory.name)}' and ALL recorded files?\nThis cannot be undone.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            shutil.rmtree(directory)
            self.refresh()
            self._sync_excel_if_available()
        except OSError as exc:
            QMessageBox.warning(self, "Delete experiment", str(exc))

    def _sync_excel_if_available(self):
        try:
            self._write_excel()
        except (ImportError, OSError, PermissionError) as exc:
            QMessageBox.warning(self, "Excel sync", f"Excel was not updated: {exc}")

    def export_excel(self):
        self.refresh()
        try:
            target = self._write_excel()
        except (ImportError, OSError, PermissionError) as exc:
            QMessageBox.warning(self, "Excel sync", str(exc))
            return
        QMessageBox.information(self, "Excel sync", f"Updated:\n{target}")

    def _write_excel(self):
        from pendulum_workbench.experiments.excel_export import export_experiment_workbook
        return export_experiment_workbook(
            self.repository.list_manifests(), self.repository.root
        )

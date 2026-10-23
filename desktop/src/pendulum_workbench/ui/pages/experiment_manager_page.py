"""Experiment browser with reversible archive and Excel index export."""
import os
import csv
import math
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QAbstractItemView,
    QFormLayout, QGroupBox, QScrollArea, QGridLayout, QFrame, QHeaderView, QSplitter,
)

from pendulum_workbench.experiments.repository import ExperimentRepository


class ExperimentManagerPage(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.repository = controller.experiment_recorder.repository
        self._records = []
        layout = QVBoxLayout(self)
        heading = QLabel("Experiment Manager")
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
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Date", "Experiment", "Mode", "Duration", "Initial angle", "Peak angle", "Status"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(True)
        self.table.horizontalHeader().setStretchLastSection(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for index, width in enumerate((150, 280, 140, 100, 115, 105, 115)):
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
            ("Recorded results", (("Initial angle", "Peak angle", "Amplification"), ("Peak time", "Samples", "Raw lines"))),
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
            ("Archive / Restore", self.toggle_archive),
            ("Delete Permanently", self.delete_selected),
        ]:
            button = QPushButton(label)
            button.clicked.connect(callback)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        controller.experiment_recorder.recording_finished.connect(lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        self.repository = self.controller.experiment_recorder.repository
        self._records = self.repository.list_manifests()
        self._filter()

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
            fields = [
                str(manifest.get("created_at") or "")[:16].replace("T", " "),
                str(manifest.get("name") or ""),
                {"VERTICAL_SINUSOIDAL_EXCITATION": "Sinusoidal",
                 "AUTO_RESONANCE_PARAMETRIC": "Auto Resonance",
                 "PHASE_TRACKED_SINE_AUTO_RESONANCE": "Phase Tracked"}.get(kind, kind),
                f"{duration:.1f} s" if isinstance(duration, (int, float)) else "—",
                self._display_number(initial, "°"),
                self._display_number(peak, "°"),
                "Archived" if archived else str(manifest.get("completion_status") or ""),
            ]
            searchable = " ".join(fields + [params, str(manifest.get("experiment_id") or "")])
            if query and query not in searchable.casefold():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate(fields):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, str(directory))
                if col in (3, 4, 5):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                elif col == 6:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                if col in (3, 4, 5):
                    numeric = (duration, initial, peak)[col - 3]
                    if isinstance(numeric, (int, float)) and math.isfinite(numeric):
                        item.setData(Qt.ItemDataRole.EditRole, float(numeric))
                        item.setData(Qt.ItemDataRole.DisplayRole, value)
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
        values = {
            "Name": manifest.get("name"),
            "Experiment ID": manifest.get("experiment_id"),
            "Created": manifest.get("created_at"),
            "Mode": details.get("experiment_mode", "Recording"),
            "Completion": manifest.get("completion_status"),
            "Archived": "Yes" if manifest.get("archived") else "No",
            "Amplitude": self._display_number(details.get("commanded_amplitude_mm"), " mm"),
            "Frequency": self._display_number(details.get("commanded_frequency_hz"), " Hz", 3),
            "Phase": self._display_number(details.get("commanded_phase_deg"), "°"),
            "Target angle": self._display_number(details.get("target_angle_deg"), "°"),
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
        }
        for key, label in self.detail_labels.items():
            value = values.get(key)
            label.setText(str(value) if value is not None and value != "" else "—")
            label.setToolTip(str(value) if value is not None else "")

    @staticmethod
    def _peak_elapsed_time(directory, manifest):
        """Rebase recorded peak time against the first valid telemetry timestamp."""
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
        from openpyxl import Workbook
        root = self.repository.root
        root.mkdir(parents=True, exist_ok=True)
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = "Experiments"
        sheet.append(["Experiment ID", "Date", "Name", "Type", "Parameters",
                      "Completion", "Archived", "Notes", "Directory"])
        for directory, manifest in self.repository.list_manifests():
            details = manifest.get("experiment") or {}
            if not isinstance(details, dict):
                details = {}
            params = ", ".join(f"{k}={v}" for k, v in details.items() if k != "experiment_mode")
            sheet.append([
                str(manifest.get("experiment_id") or ""),
                str(manifest.get("created_at") or ""),
                str(manifest.get("name") or ""),
                str(details.get("experiment_mode") or "Recording"),
                params,
                str(manifest.get("completion_status") or ""),
                "Yes" if manifest.get("archived", False) else "No",
                str(manifest.get("notes") or ""),
                str(directory),
            ])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(
                60, max(14, max(len(str(cell.value or "")) for cell in column) + 2)
            )
        target = root / "Pendulum_Experiments.xlsx"
        temporary = root / ".Pendulum_Experiments.tmp.xlsx"
        try:
            workbook.save(temporary)
            os.replace(temporary, target)
        finally:
            if temporary.exists():
                temporary.unlink()
        return target

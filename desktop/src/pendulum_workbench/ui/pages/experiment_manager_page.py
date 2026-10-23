"""Experiment browser with reversible archive and Excel index export."""
import os
import shutil
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QComboBox,
    QPushButton, QTableWidget, QTableWidgetItem, QMessageBox, QAbstractItemView,
)

from pendulum_workbench.experiments.repository import ExperimentRepository


class ExperimentManagerPage(QWidget):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller
        self.repository = controller.experiment_recorder.repository
        self._records = []
        layout = QVBoxLayout(self)
        heading = QLabel("Experiments")
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
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Date", "Name", "Type", "Parameters", "Status", "ID"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSortingEnabled(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.doubleClicked.connect(self.open_selected)
        layout.addWidget(self.table)
        actions = QHBoxLayout()
        for label, callback in [
            ("Open", self.open_selected),
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
            fields = [
                str(manifest.get("created_at") or ""),
                str(manifest.get("name") or ""),
                kind, params,
                "Archived" if archived else str(manifest.get("completion_status") or ""),
                str(manifest.get("experiment_id") or ""),
            ]
            if query and query not in " ".join(fields).casefold():
                continue
            row = self.table.rowCount()
            self.table.insertRow(row)
            for col, value in enumerate(fields):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, str(directory))
                self.table.setItem(row, col, item)
        self.summary.setText(f"{self.table.rowCount()} experiments shown")

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

    def open_selected(self, *_):
        selected = self._selected()
        if selected is None:
            return
        try:
            self.controller.open_experiment(selected[0])
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            QMessageBox.warning(self, "Open experiment", str(exc))

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

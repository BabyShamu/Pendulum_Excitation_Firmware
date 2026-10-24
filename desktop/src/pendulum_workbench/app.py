import sys
from pathlib import Path

from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.domain.models import DataSourceMode
from pendulum_workbench.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Pendulum Workbench")
    # Explicit Windows AppUserModelID helps the taskbar group and display our icon.
    if sys.platform == "win32":
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "PendulumWorkbench.ExperimentControl"
        )
    icon_path = Path(__file__).resolve().parent / "assets" / "pendulum_icon.ico"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setFont(QFont("Bahnschrift", 10))
    # Apply a consistent readable light palette to ALL popups. These
    # top-level windows otherwise inherit the Windows dark-mode background
    # while retaining the Workbench's dark text.
    app.setStyleSheet("""
        QDialog, QMessageBox, QInputDialog, QFileDialog {
            background-color: #f8faf9;
            color: #24363a;
        }
        QDialog QLabel, QMessageBox QLabel, QInputDialog QLabel, QFileDialog QLabel {
            color: #24363a;
            background-color: transparent;
        }
        QDialog QPushButton, QMessageBox QPushButton {
            color: #28463b;
            background-color: #f4f7f5;
            border: 1px solid #cbd8d0;
            padding: 7px 12px;
            border-radius: 6px;
        }
        QDialog QLineEdit, QDialog QComboBox, QDialog QTextEdit {
            color: #24363a;
            background-color: #ffffff;
            border: 1px solid #ccd8d1;
        }
    """)

    controller = WorkbenchController()
    window = MainWindow(controller)
    if icon_path.is_file():
        window.setWindowIcon(QIcon(str(icon_path)))
    controller.select_data_source(DataSourceMode.REAL)
    window.showMaximized()
    app.aboutToQuit.connect(controller.shutdown)
    return app.exec()
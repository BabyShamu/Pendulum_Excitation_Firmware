import sys

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from pendulum_workbench.application.controller import WorkbenchController
from pendulum_workbench.ui.main_window import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Pendulum Workbench")
    app.setFont(QFont("Bahnschrift", 10))

    controller = WorkbenchController()
    window = MainWindow(controller)
    controller.start_mock()
    window.show()
    app.aboutToQuit.connect(controller.stop_mock)
    return app.exec()
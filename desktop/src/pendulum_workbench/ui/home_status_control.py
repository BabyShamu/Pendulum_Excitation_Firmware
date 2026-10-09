from PySide6.QtGui import QFont
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSizePolicy

from pendulum_workbench.domain.models import HomeStatus
from pendulum_workbench.ui.status_indicator import IndicatorTone, StatusChip


class HomeStatusControl(QFrame):
    _styles = {
        HomeStatus.UNKNOWN: ("#eef0ef", "#c8cecb", "#46544f"),
        HomeStatus.HOME_REQUIRED: ("#fff0d6", "#e0ad5c", "#754707"),
        HomeStatus.HOMING: ("#edf3f8", "#a9bfd3", "#345570"),
        HomeStatus.HOMED: ("#e5f3eb", "#8fc3a3", "#205b42"),
        HomeStatus.HOMING_FAILED: ("#fbe8e6", "#d58a83", "#812f28"),
    }

    def __init__(self, status: HomeStatus = HomeStatus.UNKNOWN) -> None:
        super().__init__()
        self.setObjectName("homeStatusControl")
        self.setMinimumHeight(60)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setToolTip("Read-only homing status. Home commands are not available in this milestone.")
        self.state_label = QLabel()
        self.state_label.setObjectName("homeStatusText")
        self.state_label.setFont(QFont("Bahnschrift", 12, QFont.Weight.Bold))

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 16, 8)
        layout.addWidget(self.state_label)
        self.set_home_status(status)

    @property
    def status(self) -> HomeStatus:
        return next(
            status for status in self._styles if status.value == self.state_label.text()
        )

    def set_home_status(self, status: HomeStatus) -> None:
        background, border, foreground = self._styles[status]
        self.state_label.setText(status.value)
        self.setStyleSheet(
            "QFrame#homeStatusControl {"
            f"background: {background}; border: 1px solid {border}; border-radius: 10px;"
            "}"
            "QLabel#homeStatusText {"
            f"background: transparent; border: none; color: {foreground}; font-weight: 700;"
            "}"
        )
        self.setAccessibleName(f"Device home status: {status.value}")
        self.setAccessibleDescription(
            "Read-only homing status. This control does not send a Home command."
        )